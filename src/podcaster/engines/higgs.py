"""Higgs Audio v2 3B through mlx-audio, one voice per host cloned from a reference clip.

The model loads once per process. Each host's `voice` is the path to a short mono reference clip;
its transcript comes from the host config (`higgs_ref_text`). Long turns are split at sentence
boundaries into chunks the model can finish inside its frame cap, then concatenated.
"""

from __future__ import annotations

import re
import wave
from pathlib import Path

from podcaster.guard import cap_mlx_memory, release_mlx_cache

_MODEL = None
_MODEL_ID = None
_REFS: dict[str, object] = {}

MAX_CHUNK_CHARS = 300  # about 20 seconds of speech, well inside the 1200-frame (48s) cap
MODEL_RATE = 24000
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _model(model_id: str):
    global _MODEL, _MODEL_ID
    if _MODEL is None or _MODEL_ID != model_id:
        from mlx_audio.tts.utils import load_model

        cap_mlx_memory()
        _MODEL = load_model(model_id)
        _MODEL_ID = model_id
    return _MODEL


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Pack whole sentences into chunks of at most `max_chars`; a single longer sentence stands alone."""
    chunks: list[str] = []
    current = ""
    for sentence in SENTENCE_END.split(text.strip()):
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def _reference(path: Path):
    """Load a reference clip as float32 mono at the model rate, cached per path."""
    import numpy as np

    key = str(path)
    if key not in _REFS:
        with wave.open(key, "rb") as wf:
            rate, channels, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
        if width != 2:
            raise ValueError(f"{path}: reference clip must be 16-bit PCM")
        pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            pcm = pcm.reshape(-1, channels).mean(axis=1)
        if rate != MODEL_RATE:
            n = int(len(pcm) * MODEL_RATE / rate)
            pcm = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)
        _REFS[key] = pcm
    return _REFS[key]


def render_turn(
    text: str,
    voice: str,
    out_wav: Path,
    sample_rate: int,
    model_id: str = "mlx-community/higgs-audio-v2-3B-mlx-q8",
    speaker: dict | None = None,
    **_: object,
) -> Path:
    """Write `text` in the voice cloned from the clip at `voice` to `out_wav`, 16-bit mono."""
    import numpy as np

    model = _model(model_id)
    ref_audio = _reference(Path(voice))
    ref_text = (speaker or {}).get("higgs_ref_text")
    if not ref_text:
        raise ValueError("higgs needs higgs_ref_text (the transcript of the reference clip) in the host config")
    pieces = []
    rate = sample_rate
    for chunk in chunk_text(text):
        for result in model.generate(text=chunk, ref_audio=ref_audio, ref_text=ref_text):
            pieces.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
            rate = int(result.sample_rate)
    release_mlx_cache()
    audio = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
    return out_wav


def voice_for(speaker_cfg: dict) -> str:
    return speaker_cfg["higgs_ref_audio"]
