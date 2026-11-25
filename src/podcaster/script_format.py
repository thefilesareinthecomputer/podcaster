"""Parse a two-host script: one `[S1]`/`[S2]` turn per line, optional YAML frontmatter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TAG = re.compile(r"^\[(S[12])\]\s+(.+)$")
SPEAKERS = ("S1", "S2")


class ScriptError(ValueError):
    """A script line that does not follow the format. The message carries the 1-based line number."""


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str


def strip_frontmatter(text: str) -> tuple[dict[str, str], str, int]:
    """Return (frontmatter dict, body, number of lines consumed).

    Frontmatter is a leading block fenced by `---` lines, `key: value` per line.
    Absent frontmatter returns an empty dict, the whole text, and 0.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, 0
    meta: dict[str, str] = {}
    for i in range(1, len(lines)):
        line = lines[i].strip()
        if line == "---":
            body = "\n".join(lines[i + 1 :])
            return meta, body, i + 1
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    raise ScriptError("line 1: frontmatter opened with --- but never closed")


def parse_script(text: str) -> list[Turn]:
    """Return one Turn per tagged line. Raise ScriptError naming the first bad line."""
    _, body, offset = strip_frontmatter(text)
    turns: list[Turn] = []
    for i, raw in enumerate(body.splitlines(), start=offset + 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = TAG.match(line)
        if not m:
            raise ScriptError(f"line {i}: expected a line starting with [S1] or [S2]")
        text = m.group(2).strip()
        # A turn is passed to an engine as an argv element, and `say` reads a leading dash as
        # an option however it was quoted, so `[S1] -o /tmp/x` would redirect its output.
        # Turn text is model-generated from source material now, so this is refused here
        # rather than per engine.
        if text.startswith("-"):
            raise ScriptError(f"line {i}: a turn cannot start with '-'; it reads as an option to the engine")
        turns.append(Turn(speaker=m.group(1), text=text))
    if not turns:
        raise ScriptError("no turns found")
    return turns


def sources_from_meta(meta: dict[str, str]) -> list[Path]:
    """The script's `sources` frontmatter value as paths, in the order the writer input used.

    The value is a comma-separated list, written absolute by pipeline.py. An absent or
    empty key gives an empty list. Whether each path still exists is the caller's gate.
    """
    raw = meta.get("sources", "")
    return [Path(part.strip()) for part in raw.split(",") if part.strip()]


def word_count(turns: list[Turn]) -> int:
    return sum(len(t.text.split()) for t in turns)


OUTLINE = re.compile(r"^#\s*q:\s*(.+)$")


WORD = re.compile(r"[a-z0-9']+")


def _tokens(turns: list[Turn]) -> list[str]:
    return [w for t in turns for w in WORD.findall(t.text.lower())]


def distinct_n(tokens: list[str], n: int = 2, window: int = 100) -> float:
    """Mean over sliding windows of (unique n-grams / n-grams). 1.0 is no repetition inside any window."""
    if len(tokens) < n + 1:
        return 1.0
    scores = []
    for start in range(0, max(1, len(tokens) - window + 1), max(1, window // 2)):
        chunk = tokens[start : start + window]
        grams = [tuple(chunk[i : i + n]) for i in range(len(chunk) - n + 1)]
        if grams:
            scores.append(len(set(grams)) / len(grams))
    return sum(scores) / len(scores) if scores else 1.0


def mattr(tokens: list[str], window: int = 50) -> float:
    """Moving-average type-token ratio: lexical variety independent of length."""
    if not tokens:
        return 0.0
    if len(tokens) <= window:
        return len(set(tokens)) / len(tokens)
    ratios = [len(set(tokens[i : i + window])) / window for i in range(len(tokens) - window + 1)]
    return sum(ratios) / len(ratios)


def metrics(turns: list[Turn]) -> dict:
    """Reference-free script measures for the render log. Logged, not gated, until there is a corpus to calibrate on."""
    tokens = _tokens(turns)
    words = [len(t.text.split()) for t in turns]
    return {
        "distinct_2": round(distinct_n(tokens, 2, 100), 3),
        "mattr_50": round(mattr(tokens, 50), 3),
        "turn_words_mean": round(sum(words) / len(words), 1) if words else 0.0,
        "turn_words_max": max(words) if words else 0,
        "question_share": round(sum(t.text.rstrip().endswith("?") for t in turns) / len(turns), 3) if turns else 0.0,
        "turn_share": {s: round(sum(t.speaker == s for t in turns) / len(turns), 3) for s in SPEAKERS if turns},
    }


def section_starts(text: str) -> set[int]:
    """Indexes of turns that open a new section: a `# q:` comment sat between them and the previous turn.

    Outline lines grouped at the top mark no section (index 0 is never a boundary)."""
    _, body, _ = strip_frontmatter(text)
    starts: set[int] = set()
    pending = False
    index = 0
    for raw in body.splitlines():
        line = raw.strip()
        if OUTLINE.match(line):
            pending = True
        elif TAG.match(line):
            if pending and index > 0:
                starts.add(index)
            pending = False
            index += 1
    return starts


def gap_plan(turns: list[Turn], starts: set[int], gaps: dict) -> list[int]:
    """Milliseconds of silence before each turn after the first: question, section, closing or default."""
    default = int(gaps.get("default_ms", 350))
    plan: list[int] = []
    for i in range(1, len(turns)):
        if i in starts:
            ms = int(gaps.get("section_ms", default))
        elif i >= len(turns) - 2:
            ms = int(gaps.get("closing_ms", default))
        elif turns[i - 1].text.rstrip().endswith("?"):
            ms = int(gaps.get("after_question_ms", default))
        else:
            ms = default
        plan.append(ms)
    return plan


def outline(text: str) -> list[str]:
    """The `# q:` comment lines, in order: the sub-questions the episode answers."""
    _, body, _ = strip_frontmatter(text)
    return [m.group(1).strip() for m in (OUTLINE.match(ln.strip()) for ln in body.splitlines()) if m]


def structure_check(turns: list[Turn], questions: list[str], gate: dict) -> list[str]:
    """Structural failures, plain and numbered where a line is at fault. Empty list means pass."""
    fails: list[str] = []
    if gate.get("require_outline", True) and not questions:
        fails.append("no outline: expected `# q:` lines after the spine")
    expected = SPEAKERS[: int(gate.get("hosts", 2))]
    speakers = {t.speaker for t in turns}
    for s in SPEAKERS:
        if s in expected and s not in speakers:
            fails.append(f"speaker {s} never speaks")
        if s not in expected and s in speakers:
            fails.append(f"speaker {s} speaks in a {len(expected)}-host format")
    min_share = float(gate.get("min_turn_share", 0.3)) if len(expected) > 1 else 0.0
    for s in expected:
        share = sum(t.speaker == s for t in turns) / len(turns)
        if s in speakers and share < min_share:
            fails.append(f"speaker {s} has {share:.0%} of turns, below {min_share:.0%}")
    max_words = int(gate.get("max_turn_words", 120))
    for i, t in enumerate(turns, start=1):
        n = len(t.text.split())
        if n > max_words:
            fails.append(f"turn {i} ({t.speaker}) is {n} words, over {max_words}")
    banned = [b.lower() for b in gate.get("banned_artifacts", ["as an AI", "language model"])]
    for i, t in enumerate(turns, start=1):
        low = t.text.lower()
        for b in banned:
            if b in low:
                fails.append(f"turn {i} ({t.speaker}) contains {b!r}")
    return fails


def read_script(path: str) -> tuple[dict[str, str], list[Turn]]:
    meta, turns, _ = read_script_full(path)
    return meta, turns


def read_script_full(path: str) -> tuple[dict[str, str], list[Turn], list[str]]:
    """Frontmatter, turns and the outline questions."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    meta, _, _ = strip_frontmatter(text)
    return meta, parse_script(text), outline(text)
