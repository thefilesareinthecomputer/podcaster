"""Engine registry. Engines are imported lazily so tests run without mlx or a model."""

from __future__ import annotations

import importlib
import subprocess
from dataclasses import dataclass

from podcaster import guard


class EngineUnavailable(RuntimeError):
    """The engine cannot run on this machine. The message says why and where it runs."""


@dataclass(frozen=True)
class EngineSpec:
    """One engine's registry entry.

    `min_memory_gb` is the machine size the engine is gated at. `resident_gb` is an
    estimate of the peak resident memory one render holds on this engine, used by
    render.py's preflight. The apple and kokoro figures are estimates; higgs is gated at
    the size it has been measured on and keeps a deliberately conservative resident figure,
    since the preflight refusing a render that would have fit is the cheaper mistake.
    """

    name: str
    module: str
    min_memory_gb: int
    note: str
    resident_gb: float


ENGINES: dict[str, EngineSpec] = {
    "apple": EngineSpec("apple", "podcaster.engines.apple", 0, "macOS `say`, installed Apple neural voices", 0.5),
    "kokoro": EngineSpec("kokoro", "podcaster.engines.kokoro", 8, "Kokoro 82M via mlx-audio", 2.0),
    "higgs": EngineSpec(
        "higgs",
        "podcaster.engines.higgs",
        24,
        "Higgs Audio v2 3B via mlx-audio, voices cloned from reference clips",
        16.0,
    ),
}


def machine_memory_gb() -> int:
    out = subprocess.run([guard.SYSCTL, "-n", "hw.memsize"], capture_output=True, text=True, check=True)
    return int(out.stdout.strip()) // (1024**3)


def available_on_this_machine(memory_gb: int | None = None) -> list[str]:
    mem = machine_memory_gb() if memory_gb is None else memory_gb
    return [name for name, spec in ENGINES.items() if spec.min_memory_gb <= mem]


def load(name: str, memory_gb: int | None = None):
    """Return the engine module, or raise EngineUnavailable with a plain reason.

    Checks installed memory against the engine's minimum machine size. Free memory is not
    checked here: importing the module loads no weights, and render.py's preflight checks free
    memory against every engine's `resident_gb` plus headroom before the first turn renders."""
    if name not in ENGINES:
        raise EngineUnavailable(f"unknown engine {name!r}; choose from {', '.join(ENGINES)}")
    spec = ENGINES[name]
    mem = machine_memory_gb() if memory_gb is None else memory_gb
    if spec.min_memory_gb > mem:
        raise EngineUnavailable(
            f"{name} needs a machine with about {spec.min_memory_gb}GB unified memory and this one has {mem}GB. {spec.note}."
        )
    return importlib.import_module(spec.module)
