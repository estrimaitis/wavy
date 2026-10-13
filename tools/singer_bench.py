"""Singer-split benchmark: scenarios with a known right answer (Windows: uses the built-in TTS voices).

    python tools/singer_bench.py [work_dir]

For each scenario it builds a song, runs the pipeline and checks how many singers were found
and how cleanly each true voice landed on a single track (SDR, dB).
"""
import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import make_test_song as song  # noqa: E402

LINES = [
    "I walked along the river in the cold November rain",
    "And I could hear you calling out my name",
    "The city lights were fading as the morning came",
    "But nothing in this world will ever be the same",
    "So hold on to the moment, hold on to the night",
    "We are dancing in the shadows till the morning light",
]
HIGH = "+100%"  # about 5 semitones up: a verse-to-chorus register jump


def alternate(voice_a, voice_b, pitch_a="+0%", pitch_b="+0%"):
    return [(voice_a if i % 2 == 0 else voice_b, text, pitch_a if i % 2 == 0 else pitch_b, i * 2 * song.BAR)
            for i, text in enumerate(LINES)]


def trading_halves():
    out, t = [], 0.0
    for i, text in enumerate(LINES):
        words = text.split()
        half = len(words) // 2
        out.append(("male", " ".join(words[:half]), "+0%", ("line", i, 0)))
        out.append(("female", " ".join(words[half:]), "+0%", ("line", i, 1)))
    return out


def harmony_ending():
    lines = alternate("male", "female")[:4]
    for i, text in enumerate(LINES[4:], start=4):
        lines += [("male", text, "+0%", i * 2 * song.BAR), ("female", text, "+0%", i * 2 * song.BAR)]
    return lines


# name: (voice lines [(voice, text, pitch, start)], expected singers, voice names that should get clean tracks)
SCENARIOS = {
    "solo male, two registers": (alternate("male", "male", "+0%", HIGH), 1),
    "solo female, two registers": (alternate("female", "female", "+0%", HIGH), 1),
    "duet, line by line": (alternate("male", "female"), 2),
    "duet, high male vs female": (alternate("male", "female", HIGH, "+0%"), 2),
    "duet, trading half-lines": (trading_halves(), 2),
    "duet, harmony ending": (harmony_ending(), 2),
}


def render_lines(spec, cache: Path):
    """TTS each (voice, text, pitch) once; returns mono arrays in order."""
    todo, paths = [], []
    for voice, text, pitch, _ in spec:
        key = f"{voice}_{pitch.replace('%', 'pc').replace('+', 'p')}_{hashlib.md5(text.encode()).hexdigest()[:8]}.wav"
        path = cache / key
        paths.append(path)
        if not path.exists() and all(t["out"] != str(path) for t in todo):
            todo.append({"voice": voice, "text": text, "pitch": pitch, "out": str(path)})
    if todo:
        spec_file = cache / "spec.json"
        spec_file.write_text(json.dumps(todo), encoding="utf-8")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(ROOT / "tools" / "tts_batch.ps1"), "-Spec", str(spec_file)], check=True)
    return [song.read_wav(p) for p in paths]


def place(spec, audios):
    events, t_cursor = [], 2 * song.BAR + 0.2
    for (voice, _, _, start), audio in zip(spec, audios):
        if isinstance(start, tuple):  # back-to-back phrases
            events.append((audio, voice, t_cursor))
            t_cursor += len(audio) / song.SR + 0.15
            if start[2] == 1:
                t_cursor += 0.8
        else:
            events.append((audio, voice, 2 * song.BAR + 0.2 + start))
    return events


def sdr(ref, est):
    return 10 * np.log10((np.sum(ref ** 2) + 1e-12) / (np.sum((ref - est) ** 2) + 1e-12))


def score(job_dir: Path, truth_dir: Path, voices):
    n = sf.info(str(truth_dir / f"{voices[0]}.wav")).frames
    load = lambda p: np.pad(x := sf.read(str(p), dtype="float64", always_2d=True)[0], ((0, max(0, n - len(x))), (0, 0)))[:n]
    truth = {v: load(truth_dir / f"{v}.wav") for v in voices}
    tracks = {p.stem: load(p) for p in (job_dir / "stems").glob("singer_*.wav")}
    best = None
    for perm in itertools.permutations(tracks, min(len(tracks), len(voices))):
        s = [sdr(truth[v], tracks[t]) for v, t in zip(voices, perm)]
        if best is None or sum(s) > sum(best):
            best = s
    return best or []


def main(work=None):
    from server import env  # noqa: F401
    from server import pipeline

    work = Path(work or ROOT / "data" / "singer_bench")
    cache = work / "tts"
    cache.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, (spec, expected) in SCENARIOS.items():
        slug = name.replace(",", "").replace(" ", "_")
        audios = render_lines(spec, cache)
        voices = sorted({v for v, *_ in spec})
        song.build(place(spec, audios), work / f"{slug}.wav", work / f"{slug}_truth")
        result = pipeline.run(work / slug, work / f"{slug}.wav", {"drumkit": False, "lyrics": False}, lambda *a: None)
        found = len(result["singers"])
        sdrs = score(work / slug, work / f"{slug}_truth", voices) if found >= len(voices) else []
        ok = found == expected
        rows.append((name, expected, found, ok, sdrs, [s["median_f0"] for s in result["singers"]]))
        print(f"{'PASS' if ok else 'FAIL'}  {name:<30} expected {expected}, found {found}  "
              f"SDR {', '.join(f'{x:.1f}' for x in sdrs) or '-'}  f0 {rows[-1][5]}", flush=True)
    passed = sum(r[3] for r in rows)
    print(f"\n{passed}/{len(rows)} scenarios found the right number of singers")
    return rows


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
