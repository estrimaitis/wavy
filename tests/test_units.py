"""Fast tests: no models, no GPU."""
import numpy as np
import pytest

from server import audio_io, lyrics, singers
from tests.conftest import ffmpeg


def W(start, end, word):
    return {"start": start, "end": end, "word": word}


# ---------- lyrics ----------

def test_lines_split_on_pauses_and_sentences():
    words = [W(0.0, 0.3, "Hello"), W(0.35, 0.6, "there,"), W(0.65, 0.9, "my"), W(0.95, 1.3, "friend."),
             W(3.0, 3.3, "Second"), W(3.35, 3.7, "line")]
    lines = lyrics._to_lines(words)
    assert [ln["text"] for ln in lines] == ["Hello there, my friend.", "Second line"]
    assert lines[1]["start"] == 3.0


def test_stranded_early_word_rejoins_its_line():
    words = [W(9.4, 9.6, "And"), W(9.7, 11.5, "name."), W(12.24, 12.4, "The"),
             W(14.74, 15.0, "city"), W(15.1, 15.5, "lights")]
    lines = lyrics._to_lines(words)
    assert [ln["text"] for ln in lines] == ["And name.", "The city lights"]
    assert lines[1]["words"][0]["start"] > 14.0  # timing moved next to its line


def test_trailing_word_keeps_its_timing():
    words = [W(0.0, 0.4, "Calling"), W(0.5, 1.0, "my"), W(1.1, 1.5, "name"), W(3.0, 3.4, "yeah")]
    lines = lyrics._to_lines(words)
    assert len(lines) == 1 and lines[0]["words"][-1]["start"] == 3.0


def test_assign_singers_by_majority():
    frame = 0.02
    labels = np.full(500, -1)
    labels[0:100] = 0     # 0-2 s singer 0
    labels[150:300] = 1   # 3-6 s singer 1
    lyr = {"lines": [{"words": [W(0.1, 1.8, "a")]}, {"words": [W(3.2, 5.5, "b"), W(5.6, 5.9, "c")]}]}
    lyrics.assign_singers(lyr, labels, frame)
    assert [ln["singer"] for ln in lyr["lines"]] == [0, 1]


def test_srt_timestamps():
    lyr = {"lines": [{"start": 3661.5, "end": 3662.25, "text": "hi", "words": []}]}
    assert "01:01:01,500 --> 01:01:02,250" in lyrics.to_srt(lyr, ["A"])


def test_exports_handle_no_lines():
    empty = {"lines": []}
    assert lyrics.to_lrc(empty, []).strip() == ""
    assert lyrics.to_txt(empty, []).strip() == ""


def test_hallucination_filter_drops_words_over_silence():
    sr = 16000
    audio = np.zeros(sr * 4, dtype=np.float32)
    audio[: sr] = 0.5 * np.sin(np.arange(sr) * 2 * np.pi * 220 / sr)  # sound only in the first second
    kept = lyrics._drop_hallucinations([W(0.2, 0.6, "real"), W(2.5, 3.0, "ghost")], audio)
    assert [w["word"] for w in kept] == ["real"]


# ---------- singers ----------

def test_smooth_merges_short_turns():
    labels = np.array([0] * 100 + [1] * 10 + [0] * 100)  # a 0.2 s blip of "singer 1"
    assert set(singers._smooth(labels.copy())) == {0}


def test_smooth_keeps_real_turns():
    labels = np.array([0] * 100 + [1] * 100 + [0] * 100)
    assert list(singers._smooth(labels.copy())) == list(labels)


def test_split_silence_does_not_crash():
    out, info, labels = singers.split(np.zeros((audio_io.SR * 3, 2), dtype=np.float32))
    assert len(out) == 1 and len(info) == 1 and (labels == -1).all()


# ---------- audio io ----------

def test_peaks_shape_and_range():
    audio = np.random.default_rng(0).uniform(-1, 1, (44100, 2)).astype(np.float32)
    p = audio_io.peaks(audio, 200)
    assert len(p) == 200 and 0 <= min(p) and max(p) <= 1


def test_peaks_of_tiny_audio():
    assert len(audio_io.peaks(np.zeros((10, 2), dtype=np.float32), 50)) == 50


def test_decode_normalises_format(tmp_path):
    src = tmp_path / "mono8k.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=2", "-ac", "1", "-ar", "8000", src)
    dur = audio_io.decode_to_wav(src, tmp_path / "out.wav")
    audio = audio_io.read(tmp_path / "out.wav")
    assert abs(dur - 2.0) < 0.05 and audio.shape[1] == 2


def test_decode_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"definitely not audio")
    with pytest.raises(RuntimeError, match="couldn't be read as audio"):
        audio_io.decode_to_wav(bad, tmp_path / "out.wav")


def test_rms_db_of_silence_is_finite():
    assert audio_io.rms_db(np.zeros((100, 2))) < -150
