"""Apple neural voices through macOS `say`. Renders one turn to a wav at the requested sample rate."""

from __future__ import annotations

import subprocess
from pathlib import Path


def render_turn(text: str, voice: str, out_wav: Path, sample_rate: int, rate_wpm: int = 175, **_: object) -> Path:
    """Write `text` spoken by `voice` to `out_wav`. Uses `say` to AIFF, then ffmpeg to mono wav."""
    aiff = out_wav.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-r", str(rate_wpm), "-o", str(aiff), text], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ac", "1", "-ar", str(sample_rate), str(out_wav)],
        check=True,
    )
    aiff.unlink(missing_ok=True)
    return out_wav


def voice_for(speaker_cfg: dict) -> str:
    return speaker_cfg["apple_voice"]
