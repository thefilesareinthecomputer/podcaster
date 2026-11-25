"""The local writer: two persona contexts over one loaded model, alternating turns.

One model is loaded once. Each host gets its own conversation and its own prompt cache, so
a turn costs only the new tokens. S1 writes the spine, then opens; every turn is handed to the
other host as the next user message; when the word budget is reached each host gets one
closing turn. The loop itself (`dialogue`) takes plain callables so it is testable without mlx.

The model runs in a child process. `write()` spawns `python -m podcaster.writers.mlx_lm`,
sends one JSON payload on stdin and reads the script on stdout, then waits for the child and
reaps it. Nothing in this module loads a model in the caller's process, so a writer and a
render engine are never resident together on this machine.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from typing import Callable

from podcaster.guard import cap_mlx_memory
from podcaster.script_format import OUTLINE
from podcaster.writers import Brief, WriterUnavailable

Speak = Callable[[str], str]  # incoming text for this host -> the host's reply, raw

TAG = re.compile(r"^\s*\[S[12]\]\s*")  # matches the tag prefix; used both to strip and to recognize a turn
THINK = re.compile(r"<think>.*?</think>", re.S)


def clean_turn(raw: str, tag: str) -> str:
    """One spoken line: reasoning blocks and any tags stripped, first paragraph only, tagged."""
    text = THINK.sub("", raw).strip()
    paragraph = next((p for p in re.split(r"\n\s*\n", text) if p.strip()), "")
    line = " ".join(TAG.sub("", ln).strip() for ln in paragraph.splitlines() if ln.strip())
    return f"{tag} {line}".rstrip()


def clean_spine(raw: str) -> str:
    text = THINK.sub("", raw).strip()
    first = next((ln.strip("` \t") for ln in text.splitlines() if ln.strip() and not OUTLINE.match(ln.strip())), "")
    first = re.sub(r"^#\s*spine:\s*", "", first, flags=re.I).strip()
    return f"# spine: {first}"


def clean_header(raw: str) -> str:
    """The spine line plus any `# q:` outline lines, as the script's comment header."""
    text = THINK.sub("", raw)
    questions = [ln.strip() for ln in text.splitlines() if OUTLINE.match(ln.strip())]
    return "\n".join([clean_spine(text), *questions])


def word_count(lines: list[str]) -> int:
    return sum(len(TAG.sub("", ln).split()) for ln in lines)


MAX_EMPTY_TURNS = 3  # consecutive turns with no spoken words before the run is abandoned


class Stalled(WriterUnavailable):
    """The hosts stopped producing lines, or a host's context outgrew its ceiling."""


def dialogue(s1: Speak, s2: Speak, sources: str, word_budget: int, max_turns: int | None = None) -> tuple[str, list[str]]:
    """Run the two hosts until the budget is met, then one closing turn each. Returns (spine, lines).

    Bounded three ways: `max_turns` exchanges (default sized from the budget), MAX_EMPTY_TURNS
    consecutive wordless turns, and whatever ceiling the speakers enforce on their own context.
    An unbounded loop with a growing prompt cache is how a run eats a machine."""
    if max_turns is None:
        max_turns = max(10, word_budget // 10)  # a turn under ten words on average is a stalled run
    header_raw = s1(
        f"{sources}\n\nWrite the spine for this episode: one line, starting `# spine:`. Then three to five lines, "
        "each starting `# q:`, one sub-question the episode answers, in order. Nothing else."
    )
    spine = clean_header(header_raw)
    lines: list[str] = []
    s1_in = "Open the episode. Your first [S1] line only."
    s2_in = f"{sources}\n\n{spine}\n\nYour co-host opens the episode. Reply with your [S2] line only.\n\n"
    closing = False
    empty = 0

    def take(raw: str, tag: str) -> str:
        """Clean one reply and keep it, unless the host said nothing. Returns the kept line, or "".

        A wordless reply is counted toward the stall guard and then dropped: `[S1]` with no
        text is not a turn, and writing one produces a script the parser refuses, which turns
        a run that was otherwise fine into a failure at the last step.
        """
        nonlocal empty
        line = clean_turn(raw, tag)
        spoken = word_count([line]) > 0
        if spoken:
            lines.append(line)
        print(f"  {line[:160]}" + ("" if spoken else "  (no words; dropped)"), file=sys.stderr)
        empty = 0 if spoken else empty + 1
        if empty >= MAX_EMPTY_TURNS:
            raise Stalled(
                f"{MAX_EMPTY_TURNS} consecutive turns had no spoken words after {len(lines)} turns; stopping. "
                f"Last raw output:\n{raw[:600]}"
            )
        return line if spoken else ""

    for _ in range(max_turns):
        if closing:
            s1_in += "\n\nClose the episode in this turn: pay the spine off. Your [S1] line only."
        said = take(s1(s1_in), "[S1]")
        # A host that said nothing hands on the last line that was actually spoken, so the
        # other side still has something to answer rather than a blank.
        s2_in += said or (lines[-1] if lines else "Open the episode. Your [S2] line only.")
        if closing:
            s2_in += "\n\nThis is the last line of the episode. Your [S2] line only."
        replied = take(s2(s2_in), "[S2]")
        if closing:
            break
        closing = word_count(lines) >= word_budget
        s1_in = replied or (lines[-1] if lines else "Open the episode. Your [S1] line only.")
        s2_in = ""
    else:
        raise Stalled(f"{max_turns} exchanges without meeting the {word_budget}-word budget ({word_count(lines)} words); stopping")
    return spine, lines


def compile_episode(s1: Speak, s2: Speak, editor: Speak, sources: str, word_budget: int) -> tuple[str, list[str]]:
    """Outline, answer, compile: S1 writes spine and outline; each host answers every question in its
    own context; the editor orders the answers into tagged turns under the dedupe rule. Returns (header, lines).

    The PodAgent shape. Each host sees only its own answers, so the two minds stay apart; the
    editor sees both and cuts what they said twice."""
    header = clean_header(
        s1(
            f"{sources}\n\nWrite the spine for this episode: one line, starting `# spine:`. Then three to five lines, "
            "each starting `# q:`, one sub-question the episode answers, in order. Nothing else."
        )
    )
    questions = [ln for ln in header.splitlines() if OUTLINE.match(ln)]
    if not questions:
        raise Stalled("the outline came back without `# q:` lines; stopping")
    per_host = max(60, word_budget // (2 * len(questions)))
    answers: list[tuple[str, str, str]] = []  # (question, host tag, answer)
    for i, q in enumerate(questions):
        ask = (f"{sources}\n\n{header}\n\n" if i == 0 else "") + (
            f"{q}\n\nAnswer this in your own voice as spoken lines, about {per_host} words, from the material only. "
            "Take a stance. No tags, no headings."
        )
        for tag, host in (("[S1]", s1), ("[S2]", s2)):
            raw = THINK.sub("", host(ask)).strip()
            answers.append((q, tag, raw))
            print(f"  {tag} answered {q[:60]} ({len(raw.split())} words)", file=sys.stderr)
    material = "\n\n".join(f"{q}\n{tag} {a}" for q, tag, a in answers)
    script = editor(
        f"{header}\n\n{material}\n\nCompile these answers into the episode as [S1]/[S2] turns, one turn per line, "
        f"about {word_budget} words total. Keep each host's own words and stance; remove redundancy, preserve distinct "
        "viewpoints; the hosts react to each other; repeat each `# q:` line as a comment above the turn that opens it; "
        "pay the spine off in the last two turns. Output only the comment lines and the tagged turns."
    )
    lines = [ln.strip() for ln in THINK.sub("", script).splitlines() if TAG.match(ln.strip()) or OUTLINE.match(ln.strip())]
    spoken = [ln for ln in lines if TAG.match(ln)]
    if word_count(spoken) < word_budget * 0.4:
        raise Stalled(f"the compile pass returned {word_count(spoken)} words against a budget of {word_budget}; stopping")
    return header, lines


def _speaker(model, tokenizer, system: str, cfg: dict) -> Speak:
    """A host: its own message history and prompt cache; each call feeds only the new message."""
    from mlx_lm import generate
    from mlx_lm.models.cache import make_prompt_cache
    from mlx_lm.sample_utils import make_sampler

    cache = make_prompt_cache(model)
    sampler = make_sampler(temp=float(cfg.get("temperature", 0.8)))
    max_context = int(cfg.get("max_context_tokens", 16000))
    first = True

    def speak(incoming: str) -> str:
        nonlocal first
        messages = ([{"role": "system", "content": system}] if first else []) + [{"role": "user", "content": incoming}]
        first = False
        # Templates that know `enable_thinking` (Qwen) honor it; others ignore the extra variable.
        prompt = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, enable_thinking=bool(cfg.get("enable_thinking", False))
        )
        used = max((getattr(c, "offset", 0) for c in cache), default=0)  # hybrid models mix KV and state caches
        if used + len(prompt) + int(cfg["max_turn_tokens"]) > max_context:
            raise Stalled(f"a host's context would pass max_context_tokens={max_context} ({used} used); stopping")
        return generate(model, tokenizer, prompt, max_tokens=int(cfg["max_turn_tokens"]), prompt_cache=cache, sampler=sampler)

    return speak


def generate_script(brief: Brief, source_text: str, word_budget: int, model_id: str, options: dict) -> str:
    """Load the model, run the hosts, return the script text. This is the half that holds a model.

    Called in a child process by `write()`, never in the pipeline's own process, so the OS
    reclaims the model's memory before the render starts. Running it directly is supported
    and is what a memory measurement should do.
    """
    try:
        from mlx_lm import load
    except ImportError as e:
        raise WriterUnavailable("mlx-lm is not installed: uv sync --extra mlx_lm") from e

    t0 = time.time()
    cap_mlx_memory()
    # mlx_lm.load() defaults tokenizer_config to {"trust_remote_code": True} (utils.py:576),
    # which runs tokenizer code straight from the model repo at load time. Pass it explicitly
    # off: a model whose tokenizer needs executable code is one to decide on deliberately,
    # not one to execute because a config named it.
    model, tokenizer = load(model_id, tokenizer_config={"trust_remote_code": False})
    print(f"mlx_lm: loaded {model_id} in {time.time() - t0:.1f}s", file=sys.stderr)

    shared = brief.shared()
    s1 = _speaker(model, tokenizer, f"{shared}\n\n{brief.personas.get('[S1]', '')}", options)
    s2 = _speaker(model, tokenizer, f"{shared}\n\n{brief.personas.get('[S2]', '')}", options)
    t1 = time.time()
    if options.get("mode", "alternate") == "compile":
        editor_options = dict(options, max_turn_tokens=int(options.get("compile_tokens", 6000)))
        editor = _speaker(
            model,
            tokenizer,
            f"{shared}\n\nYou are the editor who compiles the hosts' answers into the episode.",
            editor_options,
        )
        spine, lines = compile_episode(s1, s2, editor, source_text, word_budget)
    else:
        spine, lines = dialogue(s1, s2, source_text, word_budget)
    words = word_count([ln for ln in lines if TAG.match(ln)])
    print(f"mlx_lm: {len(lines)} turns, {words} words in {time.time() - t1:.1f}s", file=sys.stderr)
    return "\n".join([spine, *lines, ""])


CHILD_TIMEOUT_S = 3600  # a write that has not finished in an hour is stuck, not slow


def write(
    brief: Brief,
    source_text: str,
    word_budget: int,
    *,
    model_id: str | None = None,
    options: dict | None = None,
) -> str:
    """Run the model in a child process and return the script it wrote.

    The process boundary is the memory mechanism, not garbage collection: a writer and an
    engine are never resident together on this machine, and only the OS can promise that
    whatever MLX did with its own allocations is given back. The child is waited for and
    reaped before this returns, so the render that follows starts on a clean machine.
    """
    if not model_id:
        raise WriterUnavailable("mlx_lm needs a model: set [writer] mlx_lm_model in config.toml")
    options = dict(options or {})
    payload = json.dumps(
        {
            "brief": {
                "mode_brief": brief.mode_brief,
                "rules": brief.rules,
                "personas": dict(brief.personas),
                "episode": brief.episode,
            },
            "source_text": source_text,
            "word_budget": word_budget,
            "model_id": model_id,
            "options": options,
        }
    )
    proc = subprocess.run(
        [sys.executable, "-m", "podcaster.writers.mlx_lm"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_S,
    )
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        raise WriterUnavailable(
            f"the mlx_lm child process exited {proc.returncode}; its output is above"
        )
    if not proc.stdout.strip():
        raise WriterUnavailable("the mlx_lm child process returned no script")
    return proc.stdout


MODEL_NAMESPACE = "mlx-community/"


def _main() -> int:
    """The child process: one JSON payload on stdin, the script on stdout, notes on stderr."""
    payload = json.loads(sys.stdin.read())
    brief = Brief(**payload["brief"])
    # AGENTS.md makes a model outside mlx-community an ask-first decision. The payload picks
    # which repo gets downloaded, so the rule is enforced here rather than only written down.
    model_id = payload["model_id"]
    if not str(model_id).startswith(MODEL_NAMESPACE):
        print(
            f"mlx_lm: refusing {model_id!r}: only {MODEL_NAMESPACE}* models are pulled; "
            "changing that is an ask-first decision in AGENTS.md",
            file=sys.stderr,
        )
        return 1
    try:
        script = generate_script(
            brief, payload["source_text"], payload["word_budget"], payload["model_id"], payload["options"]
        )
    except WriterUnavailable as e:
        print(f"mlx_lm: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(script)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
