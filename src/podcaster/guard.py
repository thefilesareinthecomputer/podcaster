"""Machine guards: one run at a time, enough free memory before a model loads, a hard cap on mlx.

A second concurrent run of the pipeline or renderer is refused with exit code 11. A writer or
engine is refused when the memory it needs is not free right now, not just installed. These
exist because two or more model loads on one machine swap it to a hang.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from contextlib import contextmanager, suppress
from pathlib import Path

# The lock is per machine, not per checkout: two clones of this repo on one Mac must
# contend for it, since the thing being guarded is the machine's memory, not the tree.
LOCK_FILE = Path.home() / ".podcaster.lock"
EXIT_BUSY = 11  # 6 is the rubric gate; every code render.py returns has one meaning
MLX_MEMORY_FRACTION = 0.75  # cap mlx at this share of physical memory so an overrun raises instead of swapping
MLX_CACHE_GB = 4  # freed Metal buffers mlx may keep; measured: uncapped, a 3B TTS model held 50GB

# Absolute, not PATH-resolved: these two decide whether a model is allowed to load, so an
# earlier entry on PATH must not be able to answer for them.
VM_STAT = "/usr/bin/vm_stat"
SYSCTL = "/usr/sbin/sysctl"


class Busy(RuntimeError):
    """Another run holds the lock. The message says which pid."""


@contextmanager
def single_instance(lock_file: Path | None = None):
    """Hold the machine lock for the duration, or raise Busy naming the holder."""
    path = Path(lock_file or LOCK_FILE)
    fh = open(path, "a+")
    # The file carries a pid and sits in the user's home; nobody else needs to read it.
    with suppress(OSError):
        os.fchmod(fh.fileno(), 0o600)
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        holder = fh.read().strip() or "unknown pid"
        fh.close()
        raise Busy(f"another run is already in progress (pid {holder}); one model load at a time on this machine")
    except OSError as e:
        # A filesystem without flock, or a lock file this user cannot take. Refusing by name
        # beats a traceback: the caller maps Busy to a clean exit code.
        fh.close()
        raise Busy(f"could not take the machine lock at {path}: {e}") from e
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _vm_stat_pages(text: str) -> dict[str, int]:
    pages = {}
    for line in text.splitlines():
        if ":" in line and line.startswith("Pages"):
            k, v = line.split(":", 1)
            pages[k.strip()] = int(v.strip().rstrip("."))
    return pages


def free_memory_gb(vm_stat_text: str | None = None, page_size: int = 16384) -> float:
    """Memory that can be claimed now: free, inactive, speculative and purgeable pages."""
    if vm_stat_text is None:
        vm_stat_text = subprocess.run([VM_STAT], capture_output=True, text=True, check=True).stdout
    p = _vm_stat_pages(vm_stat_text)
    claimable = sum(p.get(k, 0) for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
    return claimable * page_size / (1024**3)


def require_free_memory(needed_gb: float, what: str, free_gb: float | None = None) -> None:
    """Raise MemoryError with the two numbers if `needed_gb` is not free right now."""
    free = free_memory_gb() if free_gb is None else free_gb
    if free < needed_gb:
        raise MemoryError(
            f"{what} needs about {needed_gb:.0f}GB free and this machine has {free:.1f}GB free now. "
            "Another model is probably resident; wait for it to exit or kill it before retrying."
        )


def cap_mlx_memory(fraction: float = MLX_MEMORY_FRACTION) -> int:
    """Cap mlx allocations at a share of physical memory. Returns the cap in bytes. No-op without mlx."""
    try:
        import mlx.core as mx
    except ImportError:
        return 0
    total = int(subprocess.run([SYSCTL, "-n", "hw.memsize"], capture_output=True, text=True, check=True).stdout)
    cap = int(total * fraction)
    mx.set_memory_limit(cap)
    mx.set_cache_limit(MLX_CACHE_GB * 1024**3)
    print(f"guard: mlx memory capped at {cap / 1024**3:.0f}GB, buffer cache at {MLX_CACHE_GB}GB", file=sys.stderr)
    return cap


def release_mlx_cache() -> None:
    """Return freed Metal buffers to the system. Call between renders; no-op without mlx."""
    try:
        import mlx.core as mx
    except ImportError:
        return
    mx.clear_cache()
