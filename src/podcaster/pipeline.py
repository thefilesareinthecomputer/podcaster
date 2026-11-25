#!/usr/bin/env python3
"""One command from a set of source documents to an mp3.

    podgen run --source notes/ --episode A            # today's date
    podgen run --source a.md b.md --date 2026-09-17 --episode A --copy-to ~/some/folder
    podgen run --source notes/ --episode A --new-version   # start scripts/<date>-A-v<n+1>.md
    podgen run --source notes/ --episode A --mode digest   # another brief and length strategy

Steps: resolve --mode through the mode registry, resolve --source to a list of files, locate the
newest script for that date and episode, run the slop gate and render. The script itself is written
by the in-session writer from the mode's brief; when it is missing this command prints exactly what
to write and where, and exits 5.

`podgen write` is this command with --write-only: the same write step, stopping before the
render. It exits 5 with the instruction when no script exists, and 0 naming the script when one does.

Exit codes: 0 rendered (or, under --write-only, the script is already there); 4 no usable source
(a path that does not exist, or a folder with no readable source files); 5 script missing, write it;
10 the mode named by --mode or config.toml's [mode].default cannot run, refused before any source
file is read; otherwise render.py's code (1 bad script, 2 engine unavailable, 3 slop gate).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from podcaster import guard, modes, paths, render, sources, writers
from podcaster.script_format import ScriptError, parse_script, strip_frontmatter

SOURCE_SUFFIXES = sources.EXTENSIONS  # .md .txt .pdf .html .htm .url

# --length scales whatever the mode's length strategy produced. The mode owns the strategy,
# the format owns the shape, and this is the one multiplier between them.
LENGTH_SCALE = {"short": 0.5, "default": 1.0, "long": 1.6}

# The boundary marker that separates sources inside one writer input. The grounding checker
# and Synthesis mode parse this, so the format is a contract; see build_writer_input.
SOURCE_MARKER = "<<< source: {name} >>>"


class SourceError(ValueError):
    """A --source path that cannot be turned into source text. The message names the path."""


def resolve_sources(paths: list[Path], recursive: bool = False) -> list[Path]:
    """Expand --source paths to a list of files, order preserved and duplicates dropped.

    A file yields itself whatever its suffix. A folder yields its readable files sorted by
    name: .md, .txt, .pdf, .html, .htm and .url, the set sources.py knows how to read. It is
    not searched recursively unless `recursive` is set, so naming a folder cannot quietly
    pull in a subtree.
    """
    files: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path.is_file():
            found = [path]
        elif path.is_dir():
            candidates = path.rglob("*") if recursive else path.iterdir()
            # sources.readable_file skips symlinks: a folder is pointed at material, not
            # curated file by file, so a link out of it is the case to refuse.
            found = sorted(
                (p for p in candidates if sources.readable_file(p) and p.suffix.lower() in SOURCE_SUFFIXES),
                key=lambda p: p.name,
            )
            if not found:
                raise SourceError(f"{path} holds no readable source files ({', '.join(SOURCE_SUFFIXES)})")
        else:
            raise SourceError(f"{path} does not exist")
        for f in found:
            key = f.resolve()
            if key in seen:
                continue
            seen.add(key)
            files.append(f)
    return files


def build_writer_input(
    source_files: list[Path], fetch: sources.Fetch | None = None, max_words: int | None = None
) -> str:
    """Join source files into one writer input, each behind a marker naming its file.

    The contract, which FEATURE-000017's grounding checker and Synthesis mode parse:
    each source opens with a line that is exactly `<<< source: <basename> >>>`, where the
    basename is the file's name without its folders, and no other line in the input starts
    with `<<< source:`. A marker is followed by a blank line, then that source's text with
    trailing whitespace stripped; a blank line separates one source from the next. Sources
    keep the order they are given in, which is the caller's order, so the input is stable
    across runs. A source whose own text forges a marker line raises SourceError, and so
    do two sources sharing a basename, since the marker could not tell them apart.

    A source's own leading YAML frontmatter (a `---` fenced block at the top) is metadata
    about that file, not material, and is stripped before marking, so the only `---` block
    the writer sees in the instruction is the one it is told to copy. A block that opens
    and never closes is left as is.

    How a file becomes text is sources.py's business: .md and .txt as they are, a PDF through
    pypdf with a page marker each, HTML through trafilatura, and a .url file only when `fetch`
    is supplied, since nothing leaves the machine by default. `max_words` caps the finished
    input with a visible marker rather than cutting silently.
    """
    names = [s.name for s in source_files]
    for name in names:
        if names.count(name) > 1:
            raise SourceError(f"two sources share the name {name}; markers carry basenames, so rename one")
    blocks: list[str] = []
    for source in source_files:
        try:
            raw, notes = sources.read_one(source, fetch)
        except OSError:
            raise
        except Exception as e:
            # A corrupt PDF or an unreadable page skips with a note; the rest still reads.
            raw, notes = "", [f"skipped {source.name}: {type(e).__name__}: {e}"]
        for note in notes:
            print(f"sources: {note}", file=sys.stderr)
        try:
            _, text, _ = strip_frontmatter(raw)
        except ScriptError:
            text = raw
        # lstrip, because grounding_check.py strips before it decides a line is a marker: a
        # guard narrower than the parser that trusts it is a forgeable marker, and the marker
        # is what tells the checker which source a claim came from.
        if any(line.lstrip().startswith("<<< source:") for line in text.splitlines()):
            raise SourceError(f"{source} contains a line starting with '<<< source:'")
        blocks.append(f"{SOURCE_MARKER.format(name=source.name)}\n\n{text.rstrip()}")
    if max_words is not None:
        blocks = cap_blocks(blocks, max_words)
    return "\n\n".join(blocks)


def cap_blocks(blocks: list[str], max_words: int) -> list[str]:
    """Trim marked blocks to `max_words` total, keeping every marker on its own line.

    Joining the whole input and slicing words collapses it onto one line, markers included,
    so past the cap the grounding checker finds no markers at all and scores every claim
    against undifferentiated text. Cutting per block keeps the contract: each block keeps its
    marker line, a block that loses its body says so, and whole blocks past the cap are named
    rather than silently dropped.
    """
    kept: list[str] = []
    used = 0
    for i, block in enumerate(blocks):
        marker, _, body = block.partition("\n\n")
        room = max_words - used
        if room <= 0:
            dropped = len(blocks) - i
            print(f"sources: {dropped} source(s) cut entirely at [sources] max_words", file=sys.stderr)
            kept.append(f"# truncated: {dropped} source(s) cut entirely; raise [sources] max_words to keep more")
            break
        words = body.split()
        used += len(words)
        if len(words) <= room:
            kept.append(block)
            continue
        print(
            f"sources: {marker.strip()} cut to {room} words; raise [sources] max_words to keep more",
            file=sys.stderr,
        )
        kept.append(f"{marker}\n\n{' '.join(words[:room])}\n\n# truncated: {len(words) - room} words cut")
        used = max_words
    return kept


def episode_format(cfg: dict, name: str | None = None) -> dict:
    """The chosen [formats.<name>] table with its name, or a KeyError naming the choices.

    A format is the episode's shape: how many hosts, how long, and the order the writer
    follows. It is not the mode: the mode picks the brief, the length strategy and the gates.
    A config with no [formats] table still works on the flat [episode] keys.
    """
    formats = cfg.get("formats", {})
    chosen = name or cfg["episode"].get("format")
    if not formats:
        ep = cfg["episode"]
        return {"name": "default", "hosts": 2, "minutes": ep["target_minutes"], "structure": ep.get("structure", [])}
    if chosen not in formats:
        raise KeyError(f"format {chosen!r} is not in config.toml; known: {', '.join(formats)}")
    return dict(formats[chosen], name=chosen)


def latest_script(date: str, episode: str, scripts_dir: Path) -> tuple[Path | None, int]:
    """Return (newest script path, its version number); version 1 is the unsuffixed file."""
    pat = re.compile(rf"^{re.escape(date)}-{re.escape(episode)}(?:-v(\d+))?\.md$")
    best: tuple[int, Path] | None = None
    for p in scripts_dir.glob(f"{date}-{episode}*.md"):
        m = pat.match(p.name)
        if not m:
            continue
        v = int(m.group(1) or 1)
        if best is None or v > best[0]:
            best = (v, p)
    return (best[1], best[0]) if best else (None, 0)


def episode_brief(cfg: dict, fmt: dict, focus: str, words: int) -> str:
    """The shared episode brief: budget, format, structure, focus and the voices.

    Built here rather than in a writer, because every writer gets the same text. `manual`
    prints it; a model writer appends it to its instructions.
    """
    voices = {
        f"[{s.upper()}]": cfg["voices"][h["voice"]]["description"]
        for s, h in cfg.get("hosts", {}).items()
        if h.get("voice") in cfg.get("voices", {}) and cfg["voices"][h["voice"]].get("description")
    }
    wcfg = dict(
        cfg.get("writer", {}),
        structure=list(fmt["structure"]),
        hosts=int(fmt["hosts"]),
        format=fmt["name"],
        focus=focus.strip(),
        hosts_voices=voices,
    )
    return writers.brief(wcfg, words)


def build_brief(cfg: dict, mode: modes.Mode, fmt: dict, focus: str, words: int) -> writers.Brief:
    """Read every prompt part off disk and hand them to the writer as text.

    This is the one place a prompt file is opened. A writer never learns a path, a mode name
    or a config key: it gets the mode's brief, the shared rules, one persona per host and
    this run's episode instructions, and returns script text.
    """
    wcfg = cfg.get("writer", {})
    personas = {}
    for tag, key in (("[S1]", "s1"), ("[S2]", "s2")):
        path = wcfg.get(key)
        if path and Path(path).is_file():
            personas[tag] = Path(path).read_text(encoding="utf-8")
    rules_path = wcfg.get("rules")
    return writers.Brief(
        mode_brief=mode.brief.read_text(encoding="utf-8"),
        rules=Path(rules_path).read_text(encoding="utf-8") if rules_path and Path(rules_path).is_file() else "",
        personas=personas,
        episode=episode_brief(cfg, fmt, focus, words),
    )


def script_frontmatter(
    date: str, episode: str, mode_name: str, source_files: list[Path], words: int, fmt: str = ""
) -> str:
    """The block a saved script opens with, so render.py can gate it with no live handoff.

    `mode` picks the rubric weights and the grounding strategy, `format` the host count the
    structure gate expects, and `sources` the files the grounding checker reopens. Never a
    path relative to the working directory, which would only reopen from the directory the
    run happened to start in; see portable_source_path for the two spellings used instead.
    """
    lines = [
        "---",
        f"date: {date}",
        f"episode: {episode}",
        f"mode: {mode_name}",
    ]
    if fmt:
        lines.append(f"format: {fmt}")
    lines += [
        f"sources: {', '.join(portable_source_path(s) for s in source_files)}",
        f"words: {words}",
        "---",
    ]
    return "\n".join(lines)


def portable_source_path(path: Path) -> str:
    """A source path spelled so another of this user's machines can open it.

    Anything under home is written `~/...`, everything else absolute. The two machines this
    runs on have different usernames, so `/Users/<name>/...` is a path only one of them can
    open, and both the repo and the material live under home: a raw absolute path is the one
    spelling that is guaranteed not to travel.
    """
    resolved = path.resolve()
    try:
        return f"~/{resolved.relative_to(Path.home().resolve())}"
    except ValueError:
        return str(resolved)


def episode_budget(cfg: dict, mode: modes.Mode, fmt: dict | None, scale: float, source_words: int) -> int:
    """The word budget for one script: the mode's strategy, scaled by --length.

    The format supplies the minutes the `fixed` strategy reads, so --format drives length
    without the strategy knowing what a format is. A config with no [formats] table resolves
    to the flat [episode] keys, which is the same number it already used.
    """
    budget_cfg = cfg if fmt is None else dict(cfg, episode=dict(cfg.get("episode", {}), target_minutes=fmt["minutes"]))
    return round(modes.length.budget(mode, budget_cfg, source_words) * scale)


def write_instruction(
    date: str,
    episode: str,
    target: Path,
    source_files: list[Path],
    cfg: dict,
    mode: modes.Mode,
    fmt: dict | None = None,
    scale: float = 1.0,
    focus: str = "",
    writer_input: str | None = None,
) -> str:
    """The mode, brief, target, budget, frontmatter and source paths, then the writer input.

    The brief is the resolved mode's own file, not cfg["writer"]["brief"], and the word budget
    comes from the mode's length strategy, which is told how many words the combined writer
    input holds; `scale` is --length applied on top of whatever that strategy produced, so
    there is one budget calculation with two inputs rather than two that disagree. The source
    text is built by build_writer_input, whose marker format is the contract; the frontmatter
    block is printed so the writer copies it verbatim and render.py
    can resolve the mode and the sources from the saved script alone.
    """
    if writer_input is None:
        writer_input = build_writer_input(source_files)
    source_words = len(writer_input.split())
    words = episode_budget(cfg, mode, fmt, scale, source_words)
    scaled = "" if scale == 1.0 else f", scaled by {scale}"
    lines = [
        "script missing: write it now, then rerun this command.",
        f"  mode:    {mode.spec.name} ({mode.spec.length} length strategy)",
        f"  brief:   {mode.brief}",
        f"  target:  {target}",
        f"  budget:  about {words} words ({mode.spec.length} strategy{scaled}, {source_words} words of source)",
        f"  gate:    the render refuses a script that fails the slop linter; run it before saving",
        "frontmatter: the script opens with exactly this block, then the spine line and the turns.",
        # The same block a registry writer's script gets, so a hand-written script and a
        # generated one are indistinguishable to the render.
        script_frontmatter(date, episode, mode.spec.name, source_files, words, fmt["name"] if fmt else ""),
        f"words: is the budget; replace it with the finished script's own count before saving.",
    ]
    if fmt is not None:
        lines += ["", episode_brief(cfg, fmt, focus, words)]
    lines += ["", "sources:"]
    lines += [f"  {s}" for s in source_files]
    lines += ["", writer_input]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """The pipeline, wrapped in the machine lock: one model-loading process at a time."""
    try:
        with guard.single_instance():
            return run(argv)
    except guard.Busy as e:
        print(f"pipeline: {e}", file=sys.stderr)
        return guard.EXIT_BUSY


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", nargs="+", type=Path, required=True, help="one or more source files or folders (.md .txt .pdf .html .htm .url)")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--episode", default=None, help="script name key for scripts/<date>-<episode>.md (default from config.toml)")
    ap.add_argument("--engine", default=None)
    ap.add_argument("--mode", default=None, help=f"one of {', '.join(modes.names())} (default from config.toml [mode].default)")
    ap.add_argument("--copy-to", default=None)
    ap.add_argument("--new-version", action="store_true", help="ask for a fresh script version instead of rendering the newest")
    ap.add_argument("--write-only", action="store_true", help="stop after the write step; never render")
    ap.add_argument("--allow-slop", action="store_true")
    ap.add_argument("--allow-rubric-fail", action="store_true", help="passed through to the render's rubric gate")
    ap.add_argument("--allow-ungrounded", action="store_true", help="passed through to the render's grounding gate")
    ap.add_argument("--allow-structure-fail", action="store_true", help="passed through to the render's structure gate")
    ap.add_argument("--format", default=None, help="a [formats.*] table from config.toml (default [episode] format)")
    ap.add_argument("--length", default="default", choices=sorted(LENGTH_SCALE), help="scale the mode's word budget")
    ap.add_argument("--focus", default="", help="one instruction for this episode only, e.g. what to dwell on or the listener's level")
    ap.add_argument("--writer", default=None, help=f"one of {', '.join(writers.WRITERS)} (default from config.toml [writer].default)")
    ap.add_argument("--recursive", action="store_true", help="search a --source folder's subfolders too")
    ap.add_argument("--fetch", action="store_true", help="allow .url sources to be fetched (this is the only network step)")
    ap.add_argument("--show", default=None, type=Path, help="show folder holding config.toml, prompts/, voices/, scripts/, out/ (default: $PODGEN_SHOW, else ~/podcasts/default)")
    ap.add_argument("--config", default=None, type=Path, help="config file (default <show>/config.toml)")
    args = ap.parse_args(argv)

    try:
        cfg = render.load_config(args.config or paths.show_config(args.show))
    except (KeyError, OSError) as e:
        print(f"bad config: {e}", file=sys.stderr)
        return 1
    root = Path(cfg["_root"])
    scripts_dir = root / "scripts"
    episode = args.episode or cfg["episode"]["default"]

    # The mode decides the brief, the word budget and, later, the rubric weights, so it is
    # resolved before any source file is opened: an unavailable mode refuses without reading
    # or writing anything.
    try:
        mode = modes.load(args.mode or cfg["mode"]["default"], cfg)
    except modes.ModeUnavailable as e:
        print(f"mode unavailable: {e}", file=sys.stderr)
        return 10

    try:
        source_files = resolve_sources(args.source, recursive=args.recursive)
    except SourceError as e:
        print(f"no source: {e}", file=sys.stderr)
        return 4
    fetch = sources.default_fetch if args.fetch else None
    max_words = int(cfg.get("sources", {}).get("max_words", sources.DEFAULT_MAX_WORDS))

    try:
        fmt = episode_format(cfg, args.format)
    except KeyError as e:
        print(f"bad config: {e}", file=sys.stderr)
        return 1

    script, version = latest_script(args.date, episode, scripts_dir)
    if script is None or args.new_version:
        target = scripts_dir / (f"{args.date}-{episode}.md" if version == 0 else f"{args.date}-{episode}-v{version + 1}.md")
        # Built once: a .url source is fetched at most once per run, and the instruction and
        # the writer see the same text.
        try:
            writer_input = build_writer_input(source_files, fetch, max_words)
            budget = episode_budget(cfg, mode, fmt, LENGTH_SCALE[args.length], len(writer_input.split()))
            instruction = write_instruction(
                args.date,
                episode,
                target,
                source_files,
                cfg,
                mode,
                fmt,
                LENGTH_SCALE[args.length],
                args.focus,
                writer_input,
            )
        except SourceError as e:
            print(f"no source: {e}", file=sys.stderr)
            return 4
        except modes.ModeUnavailable as e:
            # A length strategy missing a config key only shows up here, once it is asked
            # for a budget; it is still a mode failure and refuses the same way.
            print(f"mode unavailable: {e}", file=sys.stderr)
            return 10
        print(instruction, file=sys.stderr)

        # The registry decides whether anything can be written here; the instruction above is
        # what a person or an in-session agent needs either way. `manual` raises at once, which
        # is today's exit 5. Any other writer produces the script, it is saved, and the run
        # continues to the gates as though the file had been dropped in by hand.
        writer_name = args.writer or cfg.get("writer", {}).get("default", "manual")
        try:
            writer = writers.load(writer_name)
        except writers.WriterUnavailable as e:
            print(f"writer unavailable: {e}", file=sys.stderr)
            return 5
        spec = writers.WRITERS.get(writer_name)
        try:
            text = writer.write(
                build_brief(cfg, mode, fmt, args.focus, budget),
                writer_input,
                budget,
                model_id=cfg.get("writer", {}).get(f"{writer_name}_model"),
                options=cfg.get("writer", {}),
            )
        except writers.WriterUnavailable as e:
            print(str(e), file=sys.stderr)
            return 5
        head = script_frontmatter(args.date, episode, mode.spec.name, source_files, budget, fmt["name"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{head}\n{text.lstrip()}", encoding="utf-8")
        try:
            parse_script(target.read_text(encoding="utf-8"))
        except ScriptError as e:
            print(f"the {writer_name} writer produced a script that does not parse: {e}", file=sys.stderr)
            print(f"it is saved at {target} so it can be read and fixed", file=sys.stderr)
            return 5
        print(
            f"pipeline: {writer_name} wrote {target.name}"
            + (" (the brief and the sources left this machine)" if spec and spec.leaves_machine else ""),
            file=sys.stderr,
        )
        script, version = target, (version + 1 if version else 1)

    if args.write_only:
        print(f"script exists: {script}")
        return 0

    print(
        f"pipeline: {len(source_files)} source(s), mode {mode.spec.name}, script {script.name} (v{version})",
        file=sys.stderr,
    )
    render_args = ["--script", str(script), "--config", str(root / "config.toml")]
    if args.engine:
        render_args += ["--engine", args.engine]
    if args.copy_to:
        render_args += ["--copy-to", args.copy_to]
    if args.allow_slop:
        render_args.append("--allow-slop")
    if args.allow_rubric_fail:
        render_args.append("--allow-rubric-fail")
    if args.allow_ungrounded:
        render_args.append("--allow-ungrounded")
    if args.allow_structure_fail:
        render_args.append("--allow-structure-fail")
    # --mode is not forwarded: the render resolves the mode from the script's own frontmatter.
    # run(), not main(): this process already holds the machine lock, and taking it twice
    # would refuse itself.
    return render.run(render_args)


if __name__ == "__main__":
    sys.exit(main())
