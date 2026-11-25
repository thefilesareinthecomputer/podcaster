#!/usr/bin/env python3
"""Score a parsed script against the structural axes of prompts/briefs/briefing.md's rubric.

Three axes, all read off the script text alone with no writer, model or engine involved:

- `spine`: a `# spine:` comment line sits between the frontmatter and the first turn.
- `turn_length`: every turn falls inside the brief's 20 to 45 word band.
- `address`: the script does not switch address mode inside a single sentence.

Findings come back in the slop linter's shape (line, category, message) so a caller can
print them the way `render.slop_gate` prints its own.

    uv run python -m podcaster.script_rubric scripts/2026-09-19-A.md
    uv run python -m podcaster.script_rubric scripts/2026-09-19-A.md --skip address,turn_length

Exit codes: 0 no findings, 1 findings or an unparseable script.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from podcaster.script_format import TAG, ScriptError, Turn, strip_frontmatter

AXES = ("spine", "turn_length", "address")
DEFAULT_WEIGHTS: dict[str, float] = {axis: 1.0 for axis in AXES}

SPINE_PREFIX = "# spine:"
DEFAULT_LOW = 20
DEFAULT_HIGH = 45

SECOND_PERSON = frozenset({"you", "your", "you're", "you've", "yours"})
# Only the masculine third person counts as a reference to the listener: prompts/briefs/briefing.md
# addresses one listener and the hosts call him "he" between themselves. she/her/hers/they/
# them/their are left out on purpose, since in these scripts they name a source or a third
# party rather than the listener, and flagging them would cost false findings.
THIRD_PERSON_LISTENER = frozenset({"he", "him", "his"})

WORD = re.compile(r"[a-z']+")
SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+")


@dataclass(frozen=True)
class Finding:
    """One rubric violation, located at a 1-based line in the script file."""

    line: int
    axis: str
    message: str

    def as_dict(self) -> dict:
        """The slop linter's finding shape, so both gates can be printed the same way."""
        return {"line": self.line, "category": self.axis, "message": self.message}


def turns_with_lines(text: str) -> list[tuple[int, Turn]]:
    """Return (file line number, Turn) pairs, walking the body the way `parse_script` does.

    `parse_script` returns turns without their line numbers, and every finding here names a
    line, so the walk is repeated rather than re-derived. Line numbers are 1-based and count
    the frontmatter, matching the numbers `ScriptError` reports.
    """
    _, body, offset = strip_frontmatter(text)
    pairs: list[tuple[int, Turn]] = []
    for i, raw in enumerate(body.splitlines(), start=offset + 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = TAG.match(line)
        if not m:
            raise ScriptError(f"line {i}: expected a line starting with [S1] or [S2]")
        pairs.append((i, Turn(speaker=m.group(1), text=m.group(2).strip())))
    if not pairs:
        raise ScriptError("no turns found")
    return pairs


def check_spine(text: str) -> list[Finding]:
    """Flag a script with no `# spine:` comment line ahead of its first turn.

    The line has to come before the first turn: a spine stated halfway down the script is
    not the header the brief asks for.
    """
    _, body, offset = strip_frontmatter(text)
    for i, raw in enumerate(body.splitlines(), start=offset + 1):
        line = raw.strip()
        if line.startswith(SPINE_PREFIX):
            return []
        if TAG.match(line):
            break
    return [Finding(line=offset + 1, axis="spine", message="no # spine: line before the first turn")]


def check_turn_length(
    pairs: list[tuple[int, Turn]], low: int = DEFAULT_LOW, high: int = DEFAULT_HIGH
) -> list[Finding]:
    """Flag every turn outside the `low` to `high` word band, naming its line and word count."""
    findings = []
    for line, turn in pairs:
        n = len(turn.text.split())
        if n < low or n > high:
            findings.append(
                Finding(
                    line=line,
                    axis="turn_length",
                    message=f"turn is {n} words, outside the {low} to {high} word band",
                )
            )
    return findings


def _sentences(text: str) -> list[str]:
    return [s for s in (part.strip() for part in SENTENCE_SPLIT.split(text)) if s]


def check_address(pairs: list[tuple[int, Turn]]) -> list[Finding]:
    """Flag a sentence that addresses the listener as "you" and describes him as "he" at once.

    prompts/briefs/briefing.md allows all three address modes in one script and asks that the script
    switch between them at turn boundaries, never mid-sentence. A turn whose sentences use
    different modes is therefore fine; one sentence carrying both is the violation.

    The heuristic is deliberately narrow. It cannot tell "you" said to the co-host from "you"
    said to the listener, so it fires only on the combination that is wrong under either
    reading: a second-person pronoun and he/him/his inside the same sentence. A mid-sentence
    switch worded some other way is missed, which is the trade taken to keep false findings
    near zero.
    """
    findings = []
    for line, turn in pairs:
        for sentence in _sentences(turn.text):
            words = set(WORD.findall(sentence.lower()))
            second = sorted(words & SECOND_PERSON)
            third = sorted(words & THIRD_PERSON_LISTENER)
            if second and third:
                findings.append(
                    Finding(
                        line=line,
                        axis="address",
                        message=(
                            f"address switches mid-sentence: {', '.join(second)} "
                            f"with {', '.join(third)} in one sentence: {sentence!r}"
                        ),
                    )
                )
    return findings


def check(
    text: str,
    rubric_weights: dict[str, float] | None = None,
    low: int = DEFAULT_LOW,
    high: int = DEFAULT_HIGH,
) -> list[Finding]:
    """Run every axis the weights do not zero out, returning findings sorted by line.

    `rubric_weights` is a mode's `ModeSpec.rubric_weights`, merged over the defaults, so a
    partial dict only changes the axes it names. A weight of 0.0 means the axis never runs,
    not that its findings are discarded. Any other magnitude behaves the same as 1.0 today;
    the number is carried for a later scored report and is not read beyond zero-or-not.

    Raises `ScriptError` when the text is not a valid script.
    """
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(rubric_weights or {})
    findings: list[Finding] = []
    if weights.get("spine", 0.0):
        findings += check_spine(text)
    if weights.get("turn_length", 0.0) or weights.get("address", 0.0):
        pairs = turns_with_lines(text)
        if weights.get("turn_length", 0.0):
            findings += check_turn_length(pairs, low=low, high=high)
        if weights.get("address", 0.0):
            findings += check_address(pairs)
    return sorted(findings, key=lambda f: (f.line, AXES.index(f.axis)))


def format_report(findings: list[Finding]) -> str:
    """The slop gate's report style: a counted header line, then one line per finding."""
    counts = {axis: sum(1 for f in findings if f.axis == axis) for axis in AXES}
    header = f"rubric: {len(findings)} finding(s); " + ", ".join(f"{axis} {counts[axis]}" for axis in AXES)
    return "\n".join([header] + [f"  {f.line} [{f.axis}] {f.message}" for f in findings])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", type=Path)
    ap.add_argument("--low", type=int, default=DEFAULT_LOW, help="lowest allowed turn word count")
    ap.add_argument("--high", type=int, default=DEFAULT_HIGH, help="highest allowed turn word count")
    ap.add_argument("--skip", default="", help=f"comma separated axes to skip: {', '.join(AXES)}")
    args = ap.parse_args(argv)

    weights = {}
    for axis in [a.strip() for a in args.skip.split(",") if a.strip()]:
        if axis not in AXES:
            print(f"unknown axis {axis!r}; known axes: {', '.join(AXES)}", file=sys.stderr)
            return 1
        weights[axis] = 0.0

    try:
        text = args.script.read_text(encoding="utf-8")
        findings = check(text, weights, low=args.low, high=args.high)
    except (ScriptError, OSError) as e:
        print(f"bad script: {e}", file=sys.stderr)
        return 1

    print(format_report(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
