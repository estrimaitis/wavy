import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import env  # noqa: E402,F401  (model cache locations)

DEMO = ROOT / "samples" / "demo_duet.wav"


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: runs the real models (GPU recommended, minutes)")


def ffmpeg(*args):
    result = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *map(str, args)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="session")
def demo():
    if not DEMO.exists():
        pytest.skip("samples/demo_duet.wav missing (setup.bat generates it on Windows)")
    return DEMO


@pytest.fixture(scope="session")
def inputs(tmp_path_factory, demo):
    """Awkward variations of the demo song, built once per test session."""
    d = tmp_path_factory.mktemp("inputs")
    made = {}

    def make(name, *args):
        out = d / name
        ffmpeg(*args, out)
        made[name] = out
        return out

    make("mono_22k.mp3", "-i", demo, "-ac", "1", "-ar", "22050", "-b:a", "96k")
    make("hires_96k.flac", "-i", demo, "-ar", "96000", "-sample_fmt", "s32")
    make("surround_51.m4a", "-i", demo, "-af", "pan=5.1|FL=c0|FR=c1|FC=0.5*c0+0.5*c1|LFE=0*c0|BL=c0|BR=c1", "-c:a", "aac")
    make("phone_8k.wav", "-i", demo, "-ac", "1", "-ar", "8000")
    make("opus.ogg", "-i", demo, "-c:a", "libopus", "-b:a", "96k")
    make("short_3s.wav", "-i", demo, "-ss", "4.5", "-t", "3")
    make("tiny_1s.wav", "-i", demo, "-ss", "5", "-t", "1")
    make("silence_30s.wav", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "30")
    make("noise_30s.wav", "-f", "lavfi", "-i", "anoisesrc=d=30:c=pink:a=0.3")
    make("video.mp4", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=40", "-i", demo,
         "-shortest", "-c:v", "libx264", "-c:a", "aac")
    make("video_no_audio.mp4", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=5", "-c:v", "libx264")
    make("Dainų demo ąčę 🎵.wav", "-i", demo)
    (d / "empty.mp3").write_bytes(b"")
    made["empty.mp3"] = d / "empty.mp3"
    return made


@pytest.fixture(autouse=True)
def _quiet_hf(monkeypatch):
    monkeypatch.setenv("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    yield


def run_pipeline(src, out_dir, **options):
    from server import pipeline

    stages = []
    result = pipeline.run(Path(out_dir), Path(src), options, lambda s, f: stages.append(s))
    return result, stages


os.environ.setdefault("PYTHONIOENCODING", "utf-8")
