"""Builds a synthetic "baked" duet with known ingredients, for testing the pipeline.

Male + female vocal lines (Windows TTS, rendered by tts_lines.ps1), drums, bass,
a synth pad and reverb on the vocals, all summed into one stereo file.

    python tools/make_test_song.py <tts_dir> <out.wav> [ground_truth_dir]

With a ground-truth dir it also writes each ingredient on its own (male, female, reverb,
drums, bass, pad), so tools/evaluate.py can score the separation.
"""
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
BPM = 100
BEAT = 60 / BPM
BAR = BEAT * 4
rng = np.random.default_rng(7)


def read_wav(path):
    with wave.open(str(path)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1)
        return data


def add(buf, sig, start_s, gain=1.0):
    i = int(start_s * SR)
    n = min(len(sig), len(buf) - i)
    if n > 0:
        buf[i:i + n] += sig[:n] * gain


def env(n, attack=0.005, decay=0.2):
    t = np.arange(n) / SR
    return np.minimum(t / attack, 1.0) * np.exp(-t / decay)


def kick():
    n = int(0.4 * SR)
    t = np.arange(n) / SR
    freq = 50 + 100 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * env(n, 0.001, 0.15)


def snare():
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n) * env(n, 0.001, 0.06)
    tone = np.sin(2 * np.pi * 190 * t) * env(n, 0.001, 0.05)
    return 0.6 * noise + 0.5 * tone


def hat():
    n = int(0.06 * SR)
    noise = rng.standard_normal(n)
    noise = np.diff(noise, prepend=0)  # crude high-pass
    return noise * env(n, 0.001, 0.015) * 0.5


def saw(freq, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    return 2 * ((t * freq) % 1.0) - 1


def lowpass(x, alpha):
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += alpha * (v - acc)
        y[i] = acc
    return y


def reverb_ir(seconds=2.2):
    """Plate-ish reverb: pre-delay, discrete early reflections, and a diffuse tail whose highs die first."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    dark = lowpass(noise, 0.08)
    dark /= np.std(dark)
    tail = noise * np.exp(-t * 6.0) * 0.5 + dark * np.exp(-t * 2.6)
    predelay = int(0.025 * SR)
    tail = np.concatenate([np.zeros(predelay), tail])[:n] * np.minimum(t / 0.06, 1)
    ir = tail * 0.6
    for delay, gain in [(0.011, 0.7), (0.019, 0.55), (0.027, 0.5), (0.041, 0.4), (0.053, 0.35), (0.071, 0.3)]:
        ir[int(delay * SR)] += gain * 30
    return ir / np.sqrt(np.sum(ir ** 2))


def convolve(x, ir):
    n = len(x) + len(ir) - 1
    size = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[:n]


def main(tts_dir, out_path, truth_dir=None):
    lines = sorted(Path(tts_dir).glob("line*.wav"))
    intro_bars = 2
    bars = intro_bars + len(lines) * 2 + 2
    total = bars * BAR + 2
    drums = np.zeros(int(total * SR))
    bass = np.zeros_like(drums)
    pad = np.zeros_like(drums)
    vox = np.zeros_like(drums)

    # A minor - F - C - G
    roots = [55.0, 43.65, 65.41, 49.0]
    chords = [[220.0, 261.63, 329.63], [174.61, 220.0, 261.63], [261.63, 329.63, 392.0], [196.0, 246.94, 293.66]]

    for bar in range(bars):
        t0 = bar * BAR
        for b in range(4):
            add(drums, kick(), t0 + b * BEAT, 0.9 if b in (0, 2) else 0.0)
            if b in (1, 3):
                add(drums, snare(), t0 + b * BEAT, 0.5)
            for h in range(2):
                add(drums, hat(), t0 + b * BEAT + h * BEAT / 2, 0.35)
        ci = bar % 4
        for b in range(4):
            note = saw(roots[ci], BEAT * 0.9) * env(int(BEAT * 0.9 * SR), 0.005, 0.4)
            add(bass, note, t0 + b * BEAT, 0.35)
        chord = sum(saw(f * 1.003, BAR) + saw(f * 0.997, BAR) for f in chords[ci])
        n = len(chord)
        fade = np.minimum(np.minimum(np.arange(n) / (0.3 * SR), 1), np.minimum((n - np.arange(n)) / (0.3 * SR), 1))
        add(pad, chord * fade, t0, 0.04)

    bass = lowpass(bass, 0.03)
    pad = lowpass(pad, 0.08)

    voices = {"male": np.zeros_like(drums), "female": np.zeros_like(drums)}
    for i, path in enumerate(lines):
        who = "female" if "female" in path.name else "male"
        add(voices[who], read_wav(path), (intro_bars + i * 2) * BAR + 0.2, 0.9)
    vox = voices["male"] + voices["female"]
    wet = convolve(vox, reverb_ir())[: len(vox)]
    wet *= np.sqrt(np.sum(vox ** 2) / np.sum(wet ** 2)) * 10 ** (-12 / 20)  # typical pop vocal: reverb ~12 dB under dry

    pad_l, pad_r = pad * 1.1, pad * 0.9
    stems = {
        "male": np.stack([voices["male"]] * 2, 1),
        "female": np.stack([voices["female"]] * 2, 1),
        "reverb": np.stack([wet] * 2, 1),
        "drums": np.stack([drums * 0.8] * 2, 1),
        "bass": np.stack([bass] * 2, 1),
        "pad": np.stack([pad_l, pad_r], 1),
    }
    mix = sum(stems.values())
    scale = 1 / (np.max(np.abs(mix)) * 1.12)
    out = Path(out_path)
    write(out, mix * scale)
    if truth_dir:
        for name, audio in stems.items():
            write(Path(truth_dir) / f"{name}.wav", audio * scale)
    print(f"wrote {out} ({total:.1f}s)")


def write(path, stereo):
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
