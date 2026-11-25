"""The manual writer: a person or an in-session agent writes the script from the prompt files.

It never produces a script itself. It raises, and the pipeline prints the full write
instruction and exits 5; rerun once the script exists. This is the registry entry for
today's default behavior, so the pipeline has no branch that only manual takes.
"""

from __future__ import annotations

from podcaster.writers import Brief, WriterUnavailable


def write(
    brief: Brief,
    source_text: str,
    word_budget: int,
    *,
    model_id: str | None = None,
    options: dict | None = None,
) -> str:
    raise WriterUnavailable("no script yet; write it from the instruction above, then rerun")
