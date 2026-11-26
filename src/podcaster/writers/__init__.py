"""Writer registry. Writers are imported lazily so tests run without mlx-lm or a model.

Every writer module exposes one function:

    write(brief: Brief, source_text: str, word_budget: int, *,
          model_id: str | None = None, options: dict | None = None) -> str

It returns the finished script text, frontmatter included, or raises WriterUnavailable with
a plain reason. No writer opens a file, reads config.toml or learns which mode it is serving:
the pipeline resolves every prompt part and hands them over as text on `brief`.

Two deliberate differences from the original WriterSpec design:

- `brief` is a Brief rather than one `brief_text` string. The design predates the split of
  the prompt surface into shared rules plus one persona file per host, and a writer that
  runs one context per host needs those parts separately. A single-context writer calls
  `brief.joined()` and gets exactly the string the design described.
- `options` carries the writer's own `[writers.<name>]` table. `model_id` alone cannot hold
  the per-turn token cap and the context ceiling, and those two are what keep a local writer
  from growing its cache until the machine swaps, so they belong in config.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Mapping

from podcaster.engines import machine_memory_gb
from podcaster.guard import HEADROOM_GB, require_free_memory


class WriterUnavailable(RuntimeError):
    """The writer cannot run here. The message says why."""


@dataclass(frozen=True)
class Brief:
    """Everything prompt-shaped one write needs, in parts, so a writer uses what it can.

    `mode_brief` is the resolved mode's brief file. `rules` is the shared prompt every mode
    uses. `personas` maps a speaker tag to that host's own prompt. `episode` is this run's
    budget, format, structure, focus and voice descriptions, built by the pipeline. The mode's
    name is deliberately absent: no rubric, grounding or mode logic crosses this boundary.
    """

    mode_brief: str = ""
    rules: str = ""
    personas: Mapping[str, str] = field(default_factory=dict)
    episode: str = ""

    def shared(self) -> str:
        """The parts every host sees: the brief, the rules and this episode's instructions."""
        return "\n\n".join(p for p in (self.mode_brief, self.rules, self.episode) if p.strip())

    def joined(self) -> str:
        """One string, for a writer that holds a single context. Personas in tag order."""
        parts = [self.mode_brief, self.rules]
        parts += [f"{tag}\n{text}" for tag, text in sorted(self.personas.items())]
        parts.append(self.episode)
        return "\n\n".join(p for p in parts if p.strip())


@dataclass(frozen=True)
class WriterSpec:
    """One writer's registry entry, the same shape as EngineSpec.

    `resident_gb` is the peak this writer holds while writing, for the preflight.
    `leaves_machine` says whether the brief and the source text go anywhere. It is recorded
    in the render log, so an episode whose text left the machine says so.
    """

    name: str
    module: str
    min_memory_gb: int
    note: str
    resident_gb: float = 0.0
    leaves_machine: bool = False


WRITERS: dict[str, WriterSpec] = {
    "manual": WriterSpec(
        "manual",
        "podcaster.writers.manual",
        0,
        "a script written by hand or in-session and dropped into scripts/",
    ),
    "mlx_lm": WriterSpec(
        "mlx_lm",
        "podcaster.writers.mlx_lm",
        24,
        "two persona contexts over one local 4-bit model via mlx-lm, in a reaped child process",
        resident_gb=18.0,
    ),
}


def brief(cfg: dict, word_budget: int) -> str:
    """The episode brief shared by every writer: budget, structure, focus, and the outline requirement."""
    lines = [f"Budget: about {word_budget} spoken words."]
    if cfg.get("format"):
        lines.append(f"Format: {cfg['format']}, {cfg.get('hosts', 2)} host(s). Put `format: {cfg['format']}` in the frontmatter.")
    if int(cfg.get("hosts", 2)) == 1:
        lines.append("Solo episode: every line is [S1]; the second persona file does not apply. Address the listener directly.")
    structure = cfg.get("structure") or []
    if structure:
        lines.append("Structure, in this order:")
        lines += [f"  {i}. {s}" for i, s in enumerate(structure, start=1)]
    if cfg.get("focus"):
        lines.append(f"Focus for this episode: {cfg['focus']}")
    if cfg.get("hosts_voices"):
        lines.append("Voices, so the words fit the mouth: " + "; ".join(f"{tag} {desc}" for tag, desc in cfg["hosts_voices"].items()))
    lines.append("After the `# spine:` line, write three to five `# q:` lines, one sub-question each, in the order the episode answers them.")
    return "\n".join(lines)


def available_on_this_machine(memory_gb: int | None = None) -> list[str]:
    mem = machine_memory_gb() if memory_gb is None else memory_gb
    return [name for name, spec in WRITERS.items() if spec.min_memory_gb <= mem]


def load(name: str, memory_gb: int | None = None, free_gb: float | None = None):
    """Return the writer module, or raise WriterUnavailable with a plain reason.

    Checks installed memory against the writer's minimum machine size, then that the writer's
    resident estimate plus headroom is free right now, so a second model never loads next to a
    resident one. The write step has no preflight of its own, so this is its only memory check."""
    if name not in WRITERS:
        raise WriterUnavailable(f"unknown writer {name!r}; choose from {', '.join(WRITERS)}")
    spec = WRITERS[name]
    mem = machine_memory_gb() if memory_gb is None else memory_gb
    if spec.min_memory_gb > mem:
        raise WriterUnavailable(
            f"{name} needs a machine with about {spec.min_memory_gb}GB unified memory and this one has {mem}GB. {spec.note}."
        )
    if spec.resident_gb:
        try:
            require_free_memory(spec.resident_gb + HEADROOM_GB, f"writer {name}", free_gb=free_gb)
        except MemoryError as e:
            raise WriterUnavailable(str(e)) from e
    return importlib.import_module(spec.module)
