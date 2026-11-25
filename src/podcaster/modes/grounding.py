"""Grounding strategies. Each one takes the script text and the marked writer input and
returns a list of findings. Register a new strategy in STRATEGIES and name it in the mode's
ModeSpec; the gate in render.py never tests a mode name.

- `none`: no claim check. The entry is None, not a function: there is nothing to call.
- `any_source`: a claim's entities and numbers appear somewhere in the combined input.
  Built in STORY-000018; see grounding_check.py for what it does and does not catch.
- `named_source`: a claim traces to the specific source it is attributed to. Designed in
  STORY-000026, not built; the entry raises so a mode naming it fails loudly rather than
  rendering as though it had been checked.

The gate wiring lands with STORY-000019. Importing this module stays stdlib-only and pulls
in no writer, engine or model: grounding_check.py is stdlib too, and is imported lazily
here so the strategy table costs nothing to read.
"""

from __future__ import annotations

from podcaster.script_rubric import Finding


def any_source(script_text: str, writer_input: str) -> list[Finding]:
    """Digest's check: every fact in a turn appears somewhere in the combined input."""
    from podcaster import grounding_check

    return grounding_check.any_source(script_text, writer_input)


def named_source(script_text: str, writer_input: str) -> list[Finding]:
    """Synthesis's check, not built."""
    raise NotImplementedError("named_source grounding is designed in STORY-000026 and not built")


# The names load() validates a ModeSpec against; iterating this gives the names, the way
# modes/length.py's table does.
STRATEGIES = {"none": None, "any_source": any_source, "named_source": named_source}


def checker(name: str):
    """The callable behind a strategy name, or None when the strategy checks nothing.

    Raises KeyError for an unregistered name; modes.load() has already rejected those.
    """
    return STRATEGIES[name]
