"""Kokoro 82M through mlx-audio. The model loads once per process; voices download from the model repo on first use."""

from __future__ import annotations

import wave
from pathlib import Path

from podcaster.guard import cap_mlx_memory, release_mlx_cache

_MODEL = None
_MODEL_ID = None


def _model(model_id: str):
    global _MODEL, _MODEL_ID
    if _MODEL is None or _MODEL_ID != model_id:
        from mlx_audio.tts.utils import load_model

        cap_mlx_memory()
        _MODEL = load_model(model_id)
        _MODEL_ID = model_id
    return _MODEL


def render_turn(
    text: str,
    voice: str,
    out_wav: Path,
    sample_rate: int,
    model_id: str = "mlx-community/Kokoro-82M-bf16",
    speed: float = 1.0,
    **_: object,
) -> Path:
    """Write `text` in `voice` to `out_wav` as 16-bit mono wav at the model's own sample rate."""
    import numpy as np

    model = _model(model_id)
    chunks = []
    rate = sample_rate
    # Kokoro voice ids start with the language pipeline letter: a = American English, b = British English.
    lang_code = voice[0] if voice[:1] in ("a", "b") else "a"
    for result in model.generate(text, voice=voice, speed=speed, lang_code=lang_code, split_pattern=r"\n+"):
        chunks.append(np.asarray(result.audio, dtype=np.float32))
        rate = int(result.sample_rate)
    release_mlx_cache()
    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
    return out_wav


def voice_for(speaker_cfg: dict) -> str:
    return speaker_cfg["kokoro_voice"]
