"""Audio decoding, reading/writing and small DSP helpers shared by the pipeline."""
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100


def decode_to_wav(src: Path, dst: Path) -> float:
    """Decode any format ffmpeg understands into 44.1 kHz stereo float WAV. Returns duration (s)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_f32le", str(dst),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg isn't installed (or isn't on PATH). Install it, restart Wavy, then retry.")
    if result.returncode != 0:
        detail = next((ln for ln in result.stderr.strip().splitlines() if "@" in ln), result.stderr.strip()[-200:])
        detail = detail.split("]", 1)[-1].strip()
        raise RuntimeError(f"This file couldn't be read as audio. It may be corrupted, or not really an audio file. ({detail})")
    info = sf.info(str(dst))
    if info.frames == 0:
        raise RuntimeError("The file decoded to zero samples. Is it really audio?")
    return info.frames / info.samplerate


def read(path: Path) -> np.ndarray:
    """Read audio as float32 (frames, 2) at 44.1 kHz."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if sr != SR:
        raise ValueError(f"{path.name}: expected {SR} Hz, got {sr}")
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    return data[:, :2]


def write(path: Path, audio: np.ndarray, final: bool = False) -> None:
    """Intermediates are float WAV (no clipping between stages); deliverables are 24-bit PCM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if final:
        sf.write(str(path), np.clip(audio, -1, 1).astype(np.float32), SR, subtype="PCM_24")
    else:
        sf.write(str(path), audio.astype(np.float32), SR, subtype="FLOAT")


def match_length(audio: np.ndarray, n: int) -> np.ndarray:
    if len(audio) >= n:
        return audio[:n]
    return np.pad(audio, ((0, n - len(audio)), (0, 0)))


def to_mono_16k(audio: np.ndarray) -> np.ndarray:
    import librosa

    mono = audio.mean(axis=1)
    return librosa.resample(mono, orig_sr=SR, target_sr=16000).astype(np.float32)


def rms_db(audio: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64))) if audio.size else 0.0
    return float(20 * np.log10(rms + 1e-9))


def peaks(audio: np.ndarray, points: int = 1600) -> list[float]:
    """Max-abs envelope for drawing waveforms, in 0..1 of full scale."""
    mono = np.abs(audio).max(axis=1) if audio.ndim == 2 else np.abs(audio)
    if len(mono) == 0:
        return [0.0] * points
    edges = np.linspace(0, len(mono), points + 1, dtype=np.int64)
    out = np.maximum.reduceat(mono, edges[:-1].clip(max=len(mono) - 1))
    return [round(float(min(v, 1.0)), 4) for v in out]
