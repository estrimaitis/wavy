"""Throw awkward inputs at the real pipeline.  pytest -m slow  (needs the models; a GPU makes it minutes)."""
import time

import pytest

from tests.conftest import ffmpeg, run_pipeline

pytestmark = pytest.mark.slow

# name -> (min lyric lines, expected singers or None to skip the check)
SONG_LIKE = {
    "mono_22k.mp3": (5, 2),
    "hires_96k.flac": (5, 2),
    "surround_51.m4a": (5, 2),
    "opus.ogg": (5, 2),
    "video.mp4": (5, 2),
    "Dainų demo ąčę 🎵.wav": (5, 2),
    "phone_8k.wav": (3, None),
}


def audible(result):
    return [t["id"] for t in result["tracks"] if not t["empty"]]


@pytest.mark.parametrize("name", list(SONG_LIKE))
def test_song_variants(name, inputs, tmp_path):
    min_lines, n_singers = SONG_LIKE[name]
    result, stages = run_pipeline(inputs[name], tmp_path)
    assert abs(result["duration"] - 40.4) < 0.5
    assert {"kick", "bass"} <= set(audible(result)), audible(result)
    assert len(result["lyrics"]["lines"]) >= min_lines
    if n_singers:
        assert len(result["singers"]) == n_singers
    assert stages[-1] == "finalize"
    assert not (tmp_path / "work").exists(), "intermediate files should be cleaned up"


@pytest.mark.parametrize("name", ["short_3s.wav", "tiny_1s.wav"])
def test_very_short_clips(name, inputs, tmp_path):
    result, _ = run_pipeline(inputs[name], tmp_path)
    assert result["duration"] < 3.5
    for t in result["tracks"]:
        assert len(t["peaks"]) > 0


def test_digital_silence(inputs, tmp_path):
    result, _ = run_pipeline(inputs["silence_30s.wav"], tmp_path)
    assert audible(result) == []
    assert result["lyrics"]["lines"] == []


def test_pure_noise_has_no_lyrics(inputs, tmp_path):
    result, _ = run_pipeline(inputs["noise_30s.wav"], tmp_path)
    assert len(result["lyrics"]["lines"]) <= 1, result["lyrics"]["lines"]


@pytest.mark.parametrize("name", ["video_no_audio.mp4", "empty.mp3"])
def test_unreadable_inputs_fail_cleanly(name, inputs, tmp_path):
    with pytest.raises(RuntimeError, match="couldn't be read as audio|zero samples|no audio"):
        run_pipeline(inputs[name], tmp_path)
    assert not (tmp_path / "work").exists(), "a failed job must not leave gigabytes behind"


def test_options_turn_stages_off(inputs, tmp_path):
    result, stages = run_pipeline(inputs["mono_22k.mp3"], tmp_path, drumkit=False, singers=False, lyrics=False)
    ids = [t["id"] for t in result["tracks"]]
    assert "drums" in ids and "kick" not in ids
    assert len(result["singers"]) == 1 and result["lyrics"]["lines"] == []
    assert "drumkit" not in stages


def test_long_song_time_and_memory(demo, tmp_path):
    """12 minutes of audio: must finish, and the server process must not balloon."""
    import psutil

    long = tmp_path / "long.flac"
    ffmpeg("-stream_loop", "17", "-i", demo, "-t", "720", long)
    proc = psutil.Process()
    peak = [proc.memory_info().rss]

    def report(stage, frac):
        peak[0] = max(peak[0], proc.memory_info().rss)

    from server import pipeline

    t0 = time.time()
    result = pipeline.run(tmp_path / "job", long, {}, report)
    elapsed = time.time() - t0
    print(f"\n12-min song: {elapsed:.0f}s, peak RSS {peak[0] / 2**30:.1f} GB, singers={len(result['singers'])}")
    assert abs(result["duration"] - 720) < 1
    assert len(result["singers"]) == 2
    assert peak[0] < 9 * 2**30  # measured 6.9 GB on the reference machine
