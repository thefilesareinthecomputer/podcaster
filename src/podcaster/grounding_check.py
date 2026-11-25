#!/usr/bin/env python3
"""Flag a script turn whose stated facts appear nowhere in the source text it was built from.

This is the `any_source` grounding strategy: a turn's facts have to appear somewhere in the
combined writer input, and nothing more. It is scoped to Digest only. A claim attributed to
the wrong source still passes here, because presence anywhere in the combined text is the
whole test; Synthesis needs a claim to trace to the source it is attributed to, and that
matching is designed separately in STORY-000026 as the `named_source` strategy.

What the check catches and what it does not: it catches an invented entity or number, one
that appears in no source. It does not catch a mischaracterized real one. A source saying
eighty percent and a script saying "nearly all" passes, since "nearly all" states no fact
this extractor can see. Read a finding as "this number or name is not in your sources", and
read a clean report as "nothing was invented out of whole cloth", not as "this is accurate".

A fact is one of three things, extracted the same way from the turn and from the source:

- A number. Digit tokens ("1961", "1.3", "91%" and "1,200", the last two read as "91" and
  "1200"), and runs of number words converted to digits, so prompts/briefs/briefing.md's spelled form
  ("ninety-one percent") matches a source written "91 percent". A run of number words ends
  at any sentence-ending punctuation and at any comma, except the comma inside one number
  ("one thousand, two hundred"), so a sentence break or a list never welds two numbers into
  a third one that nobody wrote. Numbers below ten are dropped from both sides unless they
  carry a decimal point: "one of those two instruments" is ordinary prose, not a claim, and
  small spelled numbers are far more often prose than fact. A run of number words that does not parse contributes no fact, which can miss a
  claim but never invents one.
- An acronym. A run of two or more single capital letters separated by spaces, the brief's
  spoken form ("Q C"), or an all-caps token of two to six letters ("QC"). Both normalize to
  the compact lowercase form, so the two forms match each other.
- A proper name. A run of adjacent capitalized words, with leading stopwords ("The", "A",
  "And") dropped, a trailing possessive dropped, and number words breaking the run. A run
  of two or more words counts wherever it sits. A single capitalized word counts only away
  from the start of a sentence, since the first word of a sentence is capitalized by
  grammar, not because it names anything.

Both sides are normalized the same way before comparison: accents stripped ("Jose" matches
a source's accented spelling, which is the form prompts/briefs/briefing.md asks for), lowercased,
hyphens and dashes read as spaces, whitespace collapsed. A turn's fact matches if it is in
the source's own fact set, or if it appears as a whole word inside the normalized source
text, which covers a name the extractor split differently on one side.

A turn with no facts at all is a reaction turn. It is never flagged: prompts/briefs/briefing.md asks
for those turns, and a host saying "wait, four hundred feet apart" carries the claim while
the co-host's reaction carries none.

    uv run python -m podcaster.grounding_check scripts/2026-09-19-A.md sources/one.md

Exit codes: 0 no findings, 1 findings or an unreadable script or source.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

from podcaster.script_format import ScriptError
from podcaster.script_rubric import Finding

AXIS = "grounding"

# The plain hyphen, plus the code points a pasted source can carry: hyphen, non-breaking
# hyphen, figure dash, en dash, em dash, minus sign. All read as a word break. Written by
# code point so this file holds only keyboard characters.
DASHES = "-" + "".join(chr(c) for c in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2212))

UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
NUMBER_WORDS = frozenset(UNITS) | frozenset(TENS) | frozenset(SCALES)
RUN_WORDS = NUMBER_WORDS | {"and"}

# Words that open a sentence rather than name anything. A capitalized run starting with one
# of these drops it, so "The Ashford County board" yields "ashford county".
STOPWORDS = frozenset(
    """a an and as at but by for from he her his if in is it its no not now of on or she
    so that the then there they this to wait we what when where which who why yes you your
    i s1 s2""".split()
)

# Calendar vocabulary is capitalized in English but is ordinary prose, not a claim: "closed
# it out by Thursday" and "since August" need no source, the same reasoning that drops
# numbers under ten. A calendar word never starts or joins a name run. (Added after a real
# episode, where these were most of the false findings.)
CALENDAR = frozenset(
    """monday tuesday wednesday thursday friday saturday sunday
    january february march april may june july august september october november december
    mon tue tues wed thu thur thurs fri sat sun jan feb mar apr jun jul aug sep sept oct nov dec""".split()
)

SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+")
WORD = re.compile(r"[A-Za-z][A-Za-z']*")
# A spoken acronym: single capitals separated by single spaces, at least two of them. The
# trailing word boundary keeps "I Am" from reading as "I A".
SPACED_ACRONYM = re.compile(r"\b[A-Z](?:\s[A-Z])+\b")
COMPACT_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")
DIGITS = re.compile(r"^\d+(\.\d+)?$")
# A trailing possessive or contraction is grammar, not part of the name: a script's
# "Denise's" names the same person a source's "Denise" does, and "I'll" is the pronoun "I",
# which the stopword list then drops.
POSSESSIVE = re.compile(r"'(?:s|ll|ve|m|d|re)$|'$", re.IGNORECASE)
EDGE_PUNCT = "\"'()[]{}.,;:!?%$"
# Punctuation that closes a thought, and with it any run of number words: whatever number
# word follows starts a new number.
SENTENCE_PUNCT = ".?!;:"

SOURCE_MARKER_PREFIX = "<<< source:"


def normalize(text: str) -> str:
    """Lowercase, accent-stripped, dashes read as spaces, whitespace collapsed."""
    return _prepare(text).lower()


def _prepare(text: str) -> str:
    """normalize() without the lowercasing, since capitalization is what finds a name."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    for dash in DASHES:
        stripped = stripped.replace(dash, " ")
    return " ".join(stripped.split())


def spelled_number(words: list[str] | str) -> int | None:
    """Parse a run of number words into an integer, or return None if it is not a number.

    Handles the cardinal form ("ninety two", "four hundred", "one thousand two hundred",
    with "and" tolerated between groups) and the spoken year form, two two-digit groups
    read in sequence: "nineteen sixty one" is 1961 and "twenty twenty six" is 2026. The
    cardinal reading is tried first, so "twenty six" is 26 rather than 2006.

    A sequence that does not compose ("three three") returns None rather than a guess.
    """
    if isinstance(words, str):
        words = normalize(words).split()
    words = [w for w in words]
    if not words or words[0] == "and" or words[-1] == "and":
        return None
    value = _cardinal(words)
    if value is not None:
        return value
    return _year(words)


def _cardinal(words: list[str]) -> int | None:
    """Standard cardinal composition; None the moment two words cannot stand together."""
    total = 0
    current = 0
    has_unit = False
    has_tens = False
    seen = False
    for w in words:
        if w == "and":
            if not seen:
                return None
            continue
        if w in UNITS:
            if has_unit or (has_tens and UNITS[w] >= 10):
                return None
            current += UNITS[w]
            has_unit = True
        elif w in TENS:
            if has_tens or has_unit:
                return None
            current += TENS[w]
            has_tens = True
        elif w == "hundred":
            if current == 0 or current >= 100:
                return None
            current *= 100
            has_unit = has_tens = False
        elif w in ("thousand", "million"):
            if current == 0:
                return None
            total += current * SCALES[w]
            current = 0
            has_unit = has_tens = False
        else:
            return None
        seen = True
    return total + current if seen else None


def _year(words: list[str]) -> int | None:
    """The spoken year form: a two-digit group, then a second group under one hundred."""
    if len(words) < 2 or "and" in words:
        return None
    for k in range(1, len(words)):
        head = _cardinal(words[:k])
        tail = _cardinal(words[k:])
        if head is not None and tail is not None and 10 <= head <= 99 and 0 <= tail <= 99:
            return head * 100 + tail
    return None


def _number_from_token(token: str) -> str | None:
    """A digit token as a canonical fact string, or None if the token is not a number."""
    token = token.strip(EDGE_PUNCT).replace(",", "")
    if not DIGITS.match(token):
        return None
    if "." in token:
        return token
    return str(int(token))


def _keep_number(fact: str) -> bool:
    """Numbers under ten are prose unless they carry a decimal point."""
    if "." in fact:
        return True
    return int(fact) >= 10


def _number_segments(normalized: str) -> list[tuple[bool, list[str]]]:
    """Number word runs cut at every comma and at every sentence-ending punctuation mark.

    Each segment carries whether the break that opened it was a comma, since a comma is the
    only punctuation a single number can hold. Anything else between two number words means
    one number ended and another began.
    """
    segments: list[tuple[bool, list[str]]] = []
    current: list[str] = []
    after_comma = False
    for token in normalized.split():
        word = token.strip(EDGE_PUNCT)
        trailing = token[len(token.rstrip(EDGE_PUNCT)) :]
        if word not in RUN_WORDS:
            if current:
                segments.append((after_comma, current))
            current = []
            after_comma = False
            continue
        current.append(word)
        if any(ch in SENTENCE_PUNCT for ch in trailing):
            segments.append((after_comma, current))
            current = []
            after_comma = False
        elif "," in trailing:
            segments.append((after_comma, current))
            current = []
            after_comma = True
    if current:
        segments.append((after_comma, current))
    return segments


def _number_runs(normalized: str) -> list[list[str]]:
    """Number word runs, one run per number the text is reading out.

    A comma joins two segments into one run only in the scale-and-remainder form, "one
    thousand, two hundred": the words before the comma end in a scale word and the whole
    thing composes as one cardinal number. Every other comma starts a new number, so
    "seventeen, twenty-three, and twenty-nine" is three numbers rather than the year 1723.
    """
    runs: list[list[str]] = []
    for after_comma, words in _number_segments(normalized):
        if after_comma and runs and runs[-1][-1] in SCALES and _cardinal(runs[-1] + words) is not None:
            runs[-1].extend(words)
        else:
            runs.append(words)
    return runs


def _spelled_numbers(normalized: str) -> list[str]:
    """Every number word run in the text, each read as far as it parses, as fact strings."""
    facts: list[str] = []
    for run in _number_runs(normalized):
        i = 0
        while i < len(run):
            taken = 0
            for j in range(len(run), i, -1):
                value = spelled_number(run[i:j])
                if value is not None:
                    facts.append(str(value))
                    taken = j - i
                    break
            i += taken if taken else 1
    return facts


def _names(prepared: str) -> list[str]:
    """Proper-name runs, one normalized string each. See the module docstring for the rules."""
    facts: list[str] = []
    for sentence in SENTENCE_SPLIT.split(prepared):
        matches = list(WORD.finditer(sentence))
        if not matches:
            continue
        first = matches[0].start()
        runs: list[tuple[int, list[str]]] = []
        current: list[str] = []
        start = 0
        previous_end = -2
        for m in matches:
            word = m.group(0)
            bare = POSSESSIVE.sub("", word).lower()
            capitalized = (
                len(word) > 1
                and word[0].isupper()
                and bare not in NUMBER_WORDS
                and bare not in CALENDAR
            )
            adjacent = m.start() == previous_end + 1 and sentence[previous_end : m.start()] == " "
            if capitalized and current and adjacent:
                current.append(word)
            else:
                if current:
                    runs.append((start, current))
                current = [word] if capitalized else []
                start = m.start()
            previous_end = m.end()
        if current:
            runs.append((start, current))
        for position, words in runs:
            at_start = position == first
            words = [POSSESSIVE.sub("", w) for w in words]
            while words and words[0].lower() in STOPWORDS:
                words.pop(0)
                at_start = False
            if not words:
                continue
            if len(words) == 1 and at_start:
                continue
            if len(words) == 1 and words[0].lower() in STOPWORDS:
                continue
            facts.append(" ".join(w.lower() for w in words))
    return facts


def extract_facts(text: str) -> set[str]:
    """Every number, acronym and proper name in the text, as normalized fact strings."""
    prepared = _prepare(text)
    normalized = prepared.lower()
    facts: set[str] = set()

    for token in normalized.split():
        fact = _number_from_token(token)
        if fact is not None and _keep_number(fact):
            facts.add(fact)
    for fact in _spelled_numbers(normalized):
        if _keep_number(fact):
            facts.add(fact)

    for m in SPACED_ACRONYM.finditer(prepared):
        facts.add(m.group(0).replace(" ", "").lower())
    for m in COMPACT_ACRONYM.finditer(prepared):
        facts.add(m.group(0).lower())

    facts.update(_names(prepared))
    return facts


def source_text_from_input(writer_input: str) -> str:
    """The writer input with its `<<< source: ... >>>` marker lines removed.

    The any-source strategy reads the combined input as one text, so which source a line
    came from is dropped here on purpose. named_source (STORY-000026) is the strategy that
    keeps the markers and matches per source.
    """
    kept = [line for line in writer_input.splitlines() if not line.lstrip().startswith(SOURCE_MARKER_PREFIX)]
    return "\n".join(kept)


def _sort_key(fact: str) -> tuple[int, float, str]:
    """Numbers first, in numeric order, then names and acronyms alphabetically."""
    try:
        return (0, float(fact), fact)
    except ValueError:
        return (1, 0.0, fact)


def any_source(script_text: str, writer_input: str) -> list[Finding]:
    """Flag every turn holding a fact that appears in no source, one finding per turn.

    Raises `ScriptError` when the script text is not a valid script.
    """
    from podcaster.script_rubric import turns_with_lines

    source = source_text_from_input(writer_input)
    source_facts = extract_facts(source)
    source_text = normalize(source)

    findings: list[Finding] = []
    for line, turn in turns_with_lines(script_text):
        facts = extract_facts(turn.text)
        if not facts:
            continue
        unmatched = sorted(
            (f for f in facts if not _matched(f, source_facts, source_text)), key=_sort_key
        )
        if unmatched:
            findings.append(
                Finding(
                    line=line,
                    axis=AXIS,
                    message=f"not in any source: {', '.join(repr(f) for f in unmatched)}",
                )
            )
    return sorted(findings, key=lambda f: f.line)


def _matched(fact: str, source_facts: set[str], source_text: str) -> bool:
    """A fact matches the source's own facts, or appears as a whole word in its text."""
    if fact in source_facts:
        return True
    return re.search(rf"(?<!\w){re.escape(fact)}(?!\w)", source_text) is not None


def format_report(findings: list[Finding]) -> str:
    """The rubric and slop gate report style: a counted header, then one line per finding."""
    header = f"grounding: {len(findings)} finding(s)"
    return "\n".join([header] + [f"  {f.line} [{f.axis}] {f.message}" for f in findings])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", type=Path)
    ap.add_argument("source", type=Path, nargs="+", help="the source files the script was written from")
    args = ap.parse_args(argv)

    # Imported here, not at module scope, so importing this checker never pulls in the
    # pipeline and whatever a writer drags along with it.
    from podcaster.pipeline import SourceError, build_writer_input, resolve_sources

    try:
        text = args.script.read_text(encoding="utf-8")
        writer_input = build_writer_input(resolve_sources(args.source))
        findings = any_source(text, writer_input)
    except (ScriptError, SourceError, OSError) as e:
        print(f"cannot check: {e}", file=sys.stderr)
        return 1

    print(format_report(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
