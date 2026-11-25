"""Length strategies. Each one takes the mode's config table, the whole config, and the
source word count, and returns a word budget for the writer. Register a new strategy in
STRATEGIES and name it in the mode's ModeSpec; nothing else needs to change.
"""

from __future__ import annotations

from podcaster.modes import ModeUnavailable


def _need(settings: dict, key: str, table: str):
    if key not in settings:
        raise ModeUnavailable(f"missing {key!r} in config.toml [{table}]")
    return settings[key]


def fixed(settings: dict, cfg: dict, source_words: int) -> int:
    """Target minutes times words per minute. The source length does not matter."""
    episode = cfg.get("episode", {})
    minutes = _need(episode, "target_minutes", "episode")
    wpm = _need(episode, "words_per_minute", "episode")
    return int(minutes * wpm)


def scaled(settings: dict, cfg: dict, source_words: int) -> int:
    """Source words times a ratio, clamped to the mode's floor and ceiling.

    A strategy is not told which mode it serves, so the table in its error message is
    written `modes.<mode>`; budget() fills the name in.
    """
    table = "modes.<mode>"
    ratio = _need(settings, "words_per_source_word", table)
    floor = _need(settings, "floor_words", table)
    ceiling = _need(settings, "ceiling_words", table)
    return int(max(floor, min(ceiling, round(source_words * ratio))))


STRATEGIES = {"fixed": fixed, "scaled": scaled}


def budget(mode, cfg: dict, source_words: int) -> int:
    """Word budget for one script, dispatched on the mode's length strategy name."""
    strategy = STRATEGIES.get(mode.spec.length)
    if strategy is None:
        raise ModeUnavailable(
            f"{mode.spec.name}: unknown length strategy {mode.spec.length!r}; "
            f"choose from {', '.join(STRATEGIES)} in modes/length.py"
        )
    try:
        return strategy(mode.settings, cfg, source_words)
    except ModeUnavailable as exc:
        raise ModeUnavailable(f"{mode.spec.name}: {str(exc).replace('<mode>', mode.spec.name)}") from None
