"""Mode registry. A mode picks the brief, the length strategy, the rubric weights and
the grounding strategy for one script. Briefs and strategy settings live in config.toml,
never here, so this module imports nothing but the standard library and its own two
strategy modules: importing it can never pull in a writer, mlx, or a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from podcaster import paths

# The fallback for a brief named relative to nothing in particular is the packaged template.
# render.load_config() resolves a config's brief paths against that config's own folder before
# they reach load(), so a show's briefs come in absolute and this only applies to a cfg dict
# built by hand, which is what the tests do.
BRIEF_ROOT = paths.TEMPLATE

NOT_WRITTEN = "brief designed, not yet written"


class ModeUnavailable(RuntimeError):
    """The mode cannot run. The message says which part is missing and where it is set."""


# Imported after ModeUnavailable so the strategy modules can import it from this package
# without a circular import. Both are stdlib-only.
from podcaster.modes import grounding, length  # noqa: E402


@dataclass(frozen=True)
class ModeSpec:
    """One mode's registry entry, mirroring EngineSpec.

    `length` and `grounding` are strategy names looked up in modes/length.py and
    modes/grounding.py, so a new mode adds a table row and, at most, one function.
    `rubric_weights` carries one entry per rubric axis (spine, turn_length, address);
    0.0 means the axis is never evaluated for this mode. It is a MappingProxyType
    rather than the plain dict the plan's table names, so a frozen spec cannot be
    mutated through its own field; it reads exactly like a dict.
    The brief path is not a field: it lives in config.toml and load() resolves it.
    """

    name: str
    length: str
    rubric_weights: Mapping[str, float]
    grounding: str
    note: str


def _weights(spine: float, turn_length: float, address: float) -> Mapping[str, float]:
    return MappingProxyType({"spine": spine, "turn_length": turn_length, "address": address})


MODES: dict[str, ModeSpec] = {
    "briefing": ModeSpec(
        "briefing",
        "fixed",
        _weights(1.0, 1.0, 1.0),
        "none",
        "fixed-length two-host briefing; today's brief, all three rubric axes",
    ),
    "digest": ModeSpec(
        "digest",
        "scaled",
        _weights(1.0, 1.0, 0.0),
        "any_source",
        "length scaled to the source; claims must appear somewhere in the input",
    ),
    "synthesis": ModeSpec(
        "synthesis",
        "scaled",
        _weights(1.0, 1.0, 0.0),
        "named_source",
        NOT_WRITTEN,
    ),
    "study-review": ModeSpec(
        "study-review",
        "scaled",
        _weights(1.0, 0.0, 0.0),
        "none",
        NOT_WRITTEN,
    ),
}


@dataclass(frozen=True)
class Mode:
    """A resolved mode: its registry entry, its brief on disk, and its config table."""

    spec: ModeSpec
    brief: Path
    settings: dict


def names() -> list[str]:
    """Every registered mode name, built or not."""
    return list(MODES)


def spec(name: str) -> ModeSpec:
    """Return the registry entry alone, with no brief and no config lookup.

    `render.py` needs the rubric weights and the grounding strategy of a script it already
    has, so it must not fail on a brief that was never written: a script produced under a
    mode whose brief has since moved still renders. Callers that need the brief use load().
    """
    if name not in MODES:
        raise ModeUnavailable(f"unknown mode {name!r}; choose from {', '.join(MODES)}")
    return MODES[name]


def load(name: str, cfg: dict) -> Mode:
    """Return the resolved Mode, or raise ModeUnavailable with a plain reason."""
    entry = spec(name)
    if entry.length not in length.STRATEGIES:
        raise ModeUnavailable(
            f"{name}: unknown length strategy {entry.length!r}; "
            f"choose from {', '.join(length.STRATEGIES)} in modes/length.py"
        )
    if entry.grounding not in grounding.STRATEGIES:
        raise ModeUnavailable(
            f"{name}: unknown grounding strategy {entry.grounding!r}; "
            f"choose from {', '.join(grounding.STRATEGIES)} in modes/grounding.py"
        )
    settings = cfg.get("modes", {}).get(name, {})
    brief = settings.get("brief")
    if not brief:
        raise ModeUnavailable(f"{name}: {NOT_WRITTEN}")
    path = (BRIEF_ROOT / brief).resolve() if not Path(brief).is_absolute() else Path(brief)
    if not path.is_file():
        raise ModeUnavailable(f"{name}: brief path does not exist: {path} (set in config.toml [modes.{name}])")
    return Mode(spec=entry, brief=path, settings=settings)


def available(cfg: dict) -> list[str]:
    """The mode names that resolve today. The rest are registered but not yet built."""
    ready = []
    for name in MODES:
        try:
            load(name, cfg)
        except ModeUnavailable:
            continue
        ready.append(name)
    return ready
