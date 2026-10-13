"""The unbaking pipeline: one mixed song in, a folder of isolated stems + synced lyrics out.

    mix ─┬─ vocals ── de-reverb ─┬─ reverb tail ............................ "Vocal reverb"
         │                       └─ dry ── karaoke ─┬─ lead ── diarize ──── "Singer A", "Singer B", ...
         │                                          └─ backing ............. "Backing vocals"
         └─ instrumental ── 6-stem ─┬─ drums ── drum-kit split ──────────── kick, snare, toms, hi-hat, ride, crash
                                    ├─ bass, guitar, piano, other
                                    └─ (vocal bleed) → folded into backing vocals
    dry vocals ── Whisper ── words/lines, each tagged with the singer who sings it
"""
import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio_io, lyrics, separate, singers

STAGES = [
    ("decode", "Reading the song", 2),
    ("vocals", "Lifting the vocals off the band", 14),
    ("instruments", "Splitting drums, bass, guitar, keys and synths", 22),
    ("drumkit", "Taking the drum kit apart", 12),
    ("dereverb", "Stripping the reverb off the vocals", 12),
    ("leadback", "Separating lead and backing vocals", 12),
    ("singers", "Telling the singers apart", 8),
    ("lyrics", "Writing down the lyrics", 15),
    ("finalize", "Rendering tracks", 3),
]

SINGER_COLORS = ["#ff5d8f", "#4fc3ff", "#ffb23f", "#9d7bff"]
STEM_STYLE = {
    "backing": ("Backing vocals", "voices", "#f78fd0"),
    "reverb": ("Vocal reverb", "voices", "#b7a6ff"),
    "kick": ("Kick", "drums", "#ff6b4a"),
    "snare": ("Snare", "drums", "#ff8a4a"),
    "toms": ("Toms", "drums", "#ffa54a"),
    "hihat": ("Hi-hat", "drums", "#ffc04a"),
    "ride": ("Ride", "drums", "#ffd84a"),
    "crash": ("Crash", "drums", "#f2e66b"),
    "drums": ("Drums", "drums", "#ff7a45"),
    "drums_rest": ("Other percussion", "drums", "#e0a27a"),
    "bass": ("Bass", "instruments", "#3ddc97"),
    "guitar": ("Guitar", "instruments", "#c6e05a"),
    "piano": ("Piano & keys", "instruments", "#4ee0d6"),
    "other": ("Synths & other", "instruments", "#6f9bff"),
}
SILENT_DB = -54.0  # stems quieter than this (RMS dBFS) are flagged as empty

Report = Callable[[str, float], None]


def run(job_dir: Path, source: Path, options: dict, report: Report) -> dict:
    """Process `source` into job_dir/stems. Returns the result manifest (also saved to result.json)."""
    work = job_dir / "work"
    stems_dir = job_dir / "stems"
    work.mkdir(parents=True, exist_ok=True)
    stems_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    def stage(key):
        timings[key] = time.time()
        report(key, 0.0)

    # 1. decode
    stage("decode")
    mix_path = work / "mix.wav"
    duration = audio_io.decode_to_wav(source, mix_path)
    mix = audio_io.read(mix_path)
    n = len(mix)
    audio_io.write(stems_dir / "original.wav", mix, final=True)

    # 2. vocals / instrumental
    stage("vocals")
    out = separate.run_model(separate.VOCALS_MODEL, mix_path, work, progress=lambda f: report("vocals", f))
    vocals_path = separate.pick(out, "vocals")
    inst_path = separate.pick(out, "instrumental", "other")

    # 3. instruments
    stage("instruments")
    out = separate.run_model(separate.INSTRUMENTS_MODEL, inst_path, work, progress=lambda f: report("instruments", f))
    inst = {name: audio_io.match_length(audio_io.read(p), n) for name, p in out.items()}
    bleed = inst.pop("vocals", None)

    # 4. drum kit
    drum_parts: dict[str, np.ndarray] = {}
    if options.get("drumkit", True) and "drums" in inst:
        stage("drumkit")
        drums = inst.pop("drums")
        if audio_io.rms_db(drums) > SILENT_DB:
            drums_path = work / "drums.wav"
            audio_io.write(drums_path, drums)
            out = separate.run_model(separate.DRUMKIT_MODEL, drums_path, work, progress=lambda f: report("drumkit", f))
            for name, p in out.items():
                key = {"hh": "hihat", "kick": "kick", "snare": "snare", "toms": "toms",
                       "ride": "ride", "crash": "crash"}.get(name, name)
                drum_parts[key] = audio_io.match_length(audio_io.read(p), n)
            rest = drums - sum(drum_parts.values())
            if audio_io.rms_db(rest) > audio_io.rms_db(drums) - 18:
                drum_parts["drums_rest"] = rest
        else:
            drum_parts["drums"] = drums

    # 5. de-reverb
    stage("dereverb")
    out = separate.run_model(separate.DEREVERB_MODEL, vocals_path, work, progress=lambda f: report("dereverb", f))
    # Stem names differ per de-reverb model: noreverb/reverb or dry/"no dry".
    dry_path = out.get("noreverb") or out.get("no reverb") or out.get("dry")
    reverb_path = out.get("reverb") or out.get("no dry") or out.get("other")
    if not dry_path or not reverb_path:
        raise RuntimeError(f"Unexpected de-reverb stems: {list(out)}")
    reverb = audio_io.match_length(audio_io.read(reverb_path), n)

    # 6. lead / backing
    stage("leadback")
    out = separate.run_model(separate.KARAOKE_MODEL, dry_path, work, progress=lambda f: report("leadback", f))
    lead = audio_io.match_length(audio_io.read(separate.pick(out, "vocals")), n)
    backing = audio_io.match_length(audio_io.read(separate.pick(out, "instrumental", "karaoke", "other")), n)
    if bleed is not None:
        backing = backing + bleed

    # 7. singers
    stage("singers")
    if options.get("singers", True):
        singer_audio, singer_info, labels = singers.split(lead)
    else:
        singer_audio, singer_info, labels = [lead], [singers.Singer(0, 0, 0)], np.zeros(1, dtype=int)
    singers._embedder = None  # free VRAM for Whisper

    # 8. lyrics
    stage("lyrics")
    words = {"language": None, "lines": []}
    if options.get("lyrics", True):
        dry = audio_io.match_length(audio_io.read(dry_path), n)
        words = lyrics.transcribe(dry, options.get("language") or None, progress=lambda f: report("lyrics", f))
        lyrics.unload()
        if len(singer_info) > 1:
            lyrics.assign_singers(words, labels, singers.FRAME)

    # 9. write everything out
    stage("finalize")
    tracks = []

    def emit(stem_id, audio, label, group, color, note=""):
        audio_io.write(stems_dir / f"{stem_id}.wav", audio, final=True)
        level = audio_io.rms_db(audio)
        tracks.append({
            "id": stem_id, "label": label, "group": group, "color": color, "note": note,
            "file": f"{stem_id}.wav", "rms_db": round(float(level), 1), "empty": bool(level < SILENT_DB),
            "peaks": audio_io.peaks(audio),
        })

    singer_names = []
    for s, audio in zip(singer_info, singer_audio):
        name = f"Singer {chr(65 + s.index)}" if len(singer_info) > 1 else "Lead vocal"
        singer_names.append(name)
        note = f"{s.register}" + (f" · ~{s.median_f0:.0f} Hz" if s.median_f0 else "")
        emit(f"singer_{s.index}", audio, name, "voices", SINGER_COLORS[s.index % len(SINGER_COLORS)], note)
    emit("backing", backing, *STEM_STYLE["backing"])
    # The karaoke model always leaves a little residue in "backing"; that's not backing vocals.
    if tracks[-1]["rms_db"] < audio_io.rms_db(lead) - 20:
        tracks[-1]["empty"] = True
    emit("reverb", reverb, *STEM_STYLE["reverb"])
    # Instrumentals: the vocal model always finds *something* (synth leads, leakage). If all voices
    # together sit 24 dB+ under the mix, there's no singing here, so flag the vocal tracks as silent.
    instrumental = audio_io.rms_db(lead + backing) < audio_io.rms_db(mix) - 24
    if instrumental:
        words = {"language": None, "lines": []}  # anything Whisper "heard" would be hallucinated
        for t in tracks:
            if t["group"] == "voices":
                t["empty"] = True
    for key in ["kick", "snare", "toms", "hihat", "ride", "crash", "drums", "drums_rest"]:
        if key in drum_parts:
            emit(key, drum_parts[key], *STEM_STYLE[key])
    if "drums" in inst:
        emit("drums", inst.pop("drums"), *STEM_STYLE["drums"])
    for key in ["bass", "guitar", "piano", "other"]:
        if key in inst:
            emit(key, inst.pop(key), *STEM_STYLE[key])
    for key, audio in inst.items():  # anything unexpected the model produced
        emit(key, audio, key.title(), "instruments", "#8fa3bf")

    if words["lines"]:
        (job_dir / "lyrics.lrc").write_text(lyrics.to_lrc(words, singer_names), encoding="utf-8")
        (job_dir / "lyrics.srt").write_text(lyrics.to_srt(words, singer_names), encoding="utf-8")
        (job_dir / "lyrics.txt").write_text(lyrics.to_txt(words, singer_names), encoding="utf-8")

    result = {
        "duration": duration,
        "tracks": tracks,
        "original": {"file": "original.wav", "peaks": audio_io.peaks(mix)},
        "singers": [{"name": nm, "color": SINGER_COLORS[i % len(SINGER_COLORS)], "register": s.register,
                     "median_f0": round(s.median_f0, 1), "seconds": round(s.seconds, 1)}
                    for i, (nm, s) in enumerate(zip(singer_names, singer_info))],
        "lyrics": words,
        "instrumental": bool(instrumental),
        "timings": {k: round(v, 1) for k, v in _durations(timings).items()},
    }
    (job_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    if not os.environ.get("WAVY_KEEP_WORK"):
        shutil.rmtree(work, ignore_errors=True)
    return result


def _durations(starts: dict[str, float]) -> dict[str, float]:
    keys = list(starts)
    now = time.time()
    return {k: (starts[keys[i + 1]] if i + 1 < len(keys) else now) - starts[k] for i, k in enumerate(keys)}
