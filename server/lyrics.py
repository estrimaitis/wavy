"""Time-synced lyrics from the isolated vocal, with word timings, split into lines."""
import numpy as np

from . import audio_io

MODEL = "large-v3"
_model = None


def _load():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        from . import env

        root = str(env.MODELS / "whisper")
        for device, compute in (("cuda", "float16"), ("cpu", "int8")):
            try:
                _model = WhisperModel(MODEL, device=device, compute_type=compute, download_root=root,
                                      local_files_only=True)
            except Exception:
                try:
                    _model = WhisperModel(MODEL, device=device, compute_type=compute, download_root=root)
                except Exception:
                    if device == "cpu":
                        raise
                    continue
            break
    return _model


def unload():
    global _model
    _model = None


def transcribe(vocal: np.ndarray, language: str | None = None, progress=None) -> dict:
    """Returns {"language": str, "lines": [{"start", "end", "text", "words": [{"start","end","word"}]}]}."""
    mono16 = audio_io.to_mono_16k(vocal)
    peak = float(np.max(np.abs(mono16))) if mono16.size else 0.0
    if peak < 1e-4:
        return {"language": None, "lines": []}
    mono16 = mono16 / peak * 0.9

    segments, info = _load().transcribe(
        mono16,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 300},
        beam_size=5,
        condition_on_previous_text=False,
        no_speech_threshold=0.5,
    )
    words = []
    duration = len(mono16) / 16000
    for seg in segments:
        if progress:
            progress(min(seg.end / duration, 1.0))
        if seg.no_speech_prob > 0.8 and seg.avg_logprob < -1.0:
            continue
        for w in seg.words or []:
            if w.word.strip():
                words.append({"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip(),
                              "p": round(w.probability, 3)})

    words = _drop_hallucinations(words, mono16)
    return {"language": info.language, "lines": _to_lines(words)}


def _drop_hallucinations(words: list[dict], mono16: np.ndarray) -> list[dict]:
    """Whisper invents words over silence; keep only words where the vocal is actually audible."""
    if not words:
        return words
    hop = 320
    n = len(mono16) // hop
    db = 10 * np.log10(np.mean(mono16[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    floor = np.percentile(db, 99) - 42
    kept = []
    for w in words:
        a, b = int(w["start"] * 50), max(int(w["end"] * 50), int(w["start"] * 50) + 1)
        if b > a and np.max(db[a:b], initial=-120) > floor:
            kept.append(w)
    return kept


def _to_lines(words: list[dict]) -> list[dict]:
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 99
        ends_sentence = w["word"][-1:] in ".?!"
        capital_next = nxt is not None and nxt["word"][:1].isupper() and nxt["word"] != "I" and not nxt["word"].startswith("I'")
        if gap > 0.6 or len(cur) >= 12 or ((ends_sentence or w["word"][-1:] == ",") and len(cur) >= 4) or (capital_next and len(cur) >= 4 and gap > 0.15):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    lines = _fold_stragglers(lines)
    return [
        {"start": ln[0]["start"], "end": ln[-1]["end"], "text": " ".join(w["word"] for w in ln), "words": ln}
        for ln in lines
    ]


def _fold_stragglers(lines: list[list[dict]]) -> list[list[dict]]:
    """Whisper sometimes timestamps a line's first word seconds early ("The ... city lights were fading"),
    leaving it stranded as a one-word line. Re-attach it to the line it belongs to and fix its timing."""
    out: list[list[dict]] = []
    for i, ln in enumerate(lines):
        if len(ln) == 1 and i + 1 < len(lines):
            w, nxt = ln[0], lines[i + 1]
            if (w["word"][:1].isupper() or not out) and nxt[0]["start"] - w["end"] < 4:
                if nxt[0]["start"] - w["end"] > 1.0:  # the early-timestamp glitch: move it up to its line
                    dur = min(w["end"] - w["start"], 0.5)
                    w["end"] = nxt[0]["start"]
                    w["start"] = round(w["end"] - dur, 3)
                nxt.insert(0, w)
                continue
        if len(ln) == 1 and out and ln[0]["start"] - out[-1][-1]["end"] < 2.5:
            out[-1].append(ln[0])  # a trailing word ("...my name, yeah"): keep its timing
            continue
        out.append(ln)
    return out


def assign_singers(lyrics: dict, labels: np.ndarray, frame: float) -> None:
    """Tag each line (and word) with the singer who is singing it, by majority of labelled frames."""
    for line in lyrics["lines"]:
        votes: dict[int, int] = {}
        for w in line["words"]:
            seg = labels[int(w["start"] / frame): int(w["end"] / frame) + 1]
            seg = seg[seg >= 0]
            if len(seg):
                lab = int(np.bincount(seg).argmax())
                w["singer"] = lab
                votes[lab] = votes.get(lab, 0) + len(seg)
        line["singer"] = max(votes, key=votes.get) if votes else 0


def to_lrc(lyrics: dict, names: list[str]) -> str:
    out = []
    for ln in lyrics["lines"]:
        m, s = divmod(ln["start"], 60)
        who = names[ln.get("singer", 0)] if len(names) > 1 and ln.get("singer", 0) < len(names) else ""
        out.append(f"[{int(m):02d}:{s:05.2f}]{who + ': ' if who else ''}{ln['text']}")
    return "\n".join(out) + "\n"


def to_srt(lyrics: dict, names: list[str]) -> str:
    def ts(t):
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"

    out = []
    for i, ln in enumerate(lyrics["lines"], 1):
        who = names[ln.get("singer", 0)] if len(names) > 1 and ln.get("singer", 0) < len(names) else ""
        out.append(f"{i}\n{ts(ln['start'])} --> {ts(ln['end'])}\n{('[' + who + '] ') if who else ''}{ln['text']}\n")
    return "\n".join(out)


def to_txt(lyrics: dict, names: list[str]) -> str:
    out, last = [], None
    for ln in lyrics["lines"]:
        who = ln.get("singer", 0)
        if len(names) > 1 and who != last and who < len(names):
            out.append(f"\n[{names[who]}]")
            last = who
        out.append(ln["text"])
    return "\n".join(out).strip() + "\n"
