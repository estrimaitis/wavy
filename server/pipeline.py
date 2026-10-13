"""The unbaking pipeline: one mixed song in, a folder of isolated stems + synced lyrics out.

    mix ─┬─ vocals ── de-reverb ─┬─ reverb tail ............................ "Vocal reverb"
         │                       └─ dry ── karaoke ─┬─ lead ── diarize ──── "Singer A", "Singer B", ...
         │                                          └─ backing ............. "Backing vocals"
         └─ instrumental ── 6-stem ─┬─ drums ── drum-kit split ──────────── kick, snare, toms, hi-hat, ride, crash
                                    ├─ bass, guitar, piano, other
                                    └─ (vocal bleed) → folded into backing vocals
    dry vocals ── Whisper ── words/lines, each tagged with the singer who sings it
"""
import gc
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
    try:
        return _run(job_dir, source, options, report)
    except BaseException:
        shutil.rmtree(job_dir / "stems", ignore_errors=True)  # half-written stems are useless
        raise
    finally:
        if not os.environ.get("WAVY_KEEP_WORK"):
            shutil.rmtree(job_dir / "work", ignore_errors=True)  # a few GB of intermediates per song


DISPLAY_ORDER = ["backing", "reverb", "kick", "snare", "toms", "hihat", "ride", "crash", "drums", "drums_rest",
                 "bass", "guitar", "piano", "other"]


def _display_key(track: dict):
    if track["id"].startswith("singer_"):
        return (0, int(track["id"][7:]))
    return (1, DISPLAY_ORDER.index(track["id"]) if track["id"] in DISPLAY_ORDER else len(DISPLAY_ORDER))


def _run(job_dir: Path, source: Path, options: dict, report: Report) -> dict:
    # Memory: every stem is ~250 MB per 12 minutes of audio, so each one is written out and dropped the
    # moment it's final instead of all of them waiting in RAM for the end.
    work = job_dir / "work"
    stems_dir = job_dir / "stems"
    work.mkdir(parents=True, exist_ok=True)
    stems_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    tracks: dict[str, dict] = {}

    def stage(key):
        gc.collect()
        timings[key] = time.time()
        report(key, 0.0)

    def emit(stem_id, audio, label, group, color, note=""):
        audio_io.write(stems_dir / f"{stem_id}.wav", audio, final=True)
        level = audio_io.rms_db(audio)
        tracks[stem_id] = {
            "id": stem_id, "label": label, "group": group, "color": color, "note": note,
            "file": f"{stem_id}.wav", "rms_db": round(float(level), 1), "empty": bool(level < SILENT_DB),
            "peaks": audio_io.peaks(audio),
        }

    def read(path):
        return audio_io.match_length(audio_io.read(path), n)

    # 1. decode
    stage("decode")
    mix_path = work / "mix.wav"
    duration = audio_io.decode_to_wav(source, mix_path)
    mix = audio_io.read(mix_path)
    n = len(mix)
    mix_db = audio_io.rms_db(mix)
    original_peaks = audio_io.peaks(mix)
    audio_io.write(stems_dir / "original.wav", mix, final=True)
    del mix

    # 2. vocals / instrumental
    stage("vocals")
    out = separate.run_model(separate.VOCALS_MODEL, mix_path, work, progress=lambda f: report("vocals", f))
    vocals_path = separate.pick(out, "vocals")
    inst_path = separate.pick(out, "instrumental", "other")

    # 3. instruments: everything except the drums is final straight away
    stage("instruments")
    out = separate.run_model(separate.INSTRUMENTS_MODEL, inst_path, work, progress=lambda f: report("instruments", f))
    drums_path = out.get("drums")
    bleed_path = out.get("vocals")  # vocal leakage left in the instrumental; joins the backing vocals
    for name, path in out.items():
        if name in ("drums", "vocals"):
            continue
        emit(name, read(path), *STEM_STYLE.get(name, (name.title(), "instruments", "#8fa3bf")))

    # 4. drum kit
    if drums_path:
        drums = read(drums_path)
        if options.get("drumkit", True) and audio_io.rms_db(drums) > SILENT_DB:
            stage("drumkit")
            out = separate.run_model(separate.DRUMKIT_MODEL, drums_path, work, progress=lambda f: report("drumkit", f))
            rest = drums.copy()
            for name, path in out.items():
                key = {"hh": "hihat"}.get(name, name)
                part = read(path)
                rest -= part
                emit(key, part, *STEM_STYLE.get(key, (key.title(), "drums", "#e0a27a")))
                del part
            if audio_io.rms_db(rest) > audio_io.rms_db(drums) - 18:
                emit("drums_rest", rest, *STEM_STYLE["drums_rest"])
            del rest
        else:
            emit("drums", drums, *STEM_STYLE["drums"])
        del drums

    # 5. de-reverb
    stage("dereverb")
    out = separate.run_model(separate.DEREVERB_MODEL, vocals_path, work, progress=lambda f: report("dereverb", f))
    # Stem names differ per de-reverb model: noreverb/reverb or dry/"no dry".
    dry_path = out.get("noreverb") or out.get("no reverb") or out.get("dry")
    reverb_path = out.get("reverb") or out.get("no dry") or out.get("other")
    if not dry_path or not reverb_path:
        raise RuntimeError(f"Unexpected de-reverb stems: {list(out)}")
    emit("reverb", read(reverb_path), *STEM_STYLE["reverb"])

    # 6. lead / backing
    stage("leadback")
    out = separate.run_model(separate.KARAOKE_MODEL, dry_path, work, progress=lambda f: report("leadback", f))
    lead = read(separate.pick(out, "vocals"))
    backing = read(separate.pick(out, "instrumental", "karaoke", "other"))
    if bleed_path:
        backing += read(bleed_path)
    lead_db = audio_io.rms_db(lead)
    voices_db = audio_io.rms_db(lead + backing)
    emit("backing", backing, *STEM_STYLE["backing"])
    del backing
    # The karaoke model always leaves a little residue in "backing"; that's not backing vocals.
    if tracks["backing"]["rms_db"] < lead_db - 20:
        tracks["backing"]["empty"] = True

    # 7. singers
    stage("singers")
    if options.get("singers", True):
        singer_audio, singer_info, labels = singers.split(lead)
    else:
        singer_audio, singer_info, labels = [lead], [singers.Singer(0, 0, 0)], np.zeros(1, dtype=int)
    singers._embedder = None  # free VRAM for Whisper
    del lead
    singer_names = []
    for s, audio in zip(singer_info, singer_audio):
        name = f"Singer {chr(65 + s.index)}" if len(singer_info) > 1 else "Lead vocal"
        singer_names.append(name)
        note = f"{s.register}" + (f" · ~{s.median_f0:.0f} Hz" if s.median_f0 else "")
        emit(f"singer_{s.index}", audio, name, "voices", SINGER_COLORS[s.index % len(SINGER_COLORS)], note)
    del singer_audio
    audio = None

    # Instrumentals: the vocal model always finds *something* (synth leads, leakage). If all voices
    # together sit 24 dB+ under the mix, there's no singing here, so flag the vocal tracks as silent.
    instrumental = voices_db < mix_db - 24
    if instrumental:
        for t in tracks.values():
            if t["group"] == "voices":
                t["empty"] = True

    # 8. lyrics. The process holds almost no audio by now, which leaves room for Whisper.
    stage("lyrics")
    words = {"language": None, "lines": []}
    if options.get("lyrics", True) and not instrumental:  # on instrumentals Whisper would only hallucinate
        words = lyrics.transcribe(read(dry_path), options.get("language") or None,
                                  progress=lambda f: report("lyrics", f))
        lyrics.unload()
        if len(singer_info) > 1:
            lyrics.assign_singers(words, labels, singers.FRAME)

    # 9. write everything out
    stage("finalize")
    if words["lines"]:
        (job_dir / "lyrics.lrc").write_text(lyrics.to_lrc(words, singer_names), encoding="utf-8")
        (job_dir / "lyrics.srt").write_text(lyrics.to_srt(words, singer_names), encoding="utf-8")
        (job_dir / "lyrics.txt").write_text(lyrics.to_txt(words, singer_names), encoding="utf-8")

    result = {
        "duration": duration,
        "tracks": sorted(tracks.values(), key=_display_key),
        "original": {"file": "original.wav", "peaks": original_peaks},
        "singers": [{"name": nm, "color": SINGER_COLORS[i % len(SINGER_COLORS)], "register": s.register,
                     "median_f0": round(s.median_f0, 1), "seconds": round(s.seconds, 1)}
                    for i, (nm, s) in enumerate(zip(singer_names, singer_info))],
        "lyrics": words,
        "instrumental": bool(instrumental),
        "timings": {k: round(v, 1) for k, v in _durations(timings).items()},
    }
    (job_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def _durations(starts: dict[str, float]) -> dict[str, float]:
    keys = list(starts)
    now = time.time()
    return {k: (starts[keys[i + 1]] if i + 1 < len(keys) else now) - starts[k] for i, k in enumerate(keys)}
