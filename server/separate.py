"""Thin wrapper over python-audio-separator: run one model, get back {stem name: path}."""
import gc
import logging
import re
import shutil
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models"

# Best-in-class open models for each split (all run through audio-separator).
VOCALS_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
INSTRUMENTS_MODEL = "BS-Roformer-SW.ckpt"           # vocals/drums/bass/guitar/piano/other
DRUMKIT_MODEL = "MDX23C-DrumSep-aufr33-jarredou.ckpt"  # kick/snare/toms/hh/ride/crash
# Scored best on our ground-truth test (tools/evaluate.py): the Roformer de-reverbs barely touched
# a typical plate reverb, MDX23C removed it (dry-vocal SDR 11.9 -> 14.6 dB).
DEREVERB_MODEL = "MDX23C-De-Reverb-aufr33-jarredou.ckpt"
KARAOKE_MODEL = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"

_STEM_RE = re.compile(r"_\(([^)]+)\)")


@contextmanager
def _tqdm_progress(callback):
    """audio-separator reports progress only through tqdm bars; forward them to `callback(0..1)`."""
    from tqdm import tqdm

    original = tqdm.update
    best = [0.0]

    def update(self, n=1):
        result = original(self, n)
        if callback and self.total:
            frac = min(self.n / self.total, 1.0)
            if frac > best[0]:
                best[0] = frac
                callback(frac)
        return result

    tqdm.update = update
    try:
        yield
    finally:
        tqdm.update = original


def run_model(model: str, input_path: Path, out_dir: Path, progress=None) -> dict[str, Path]:
    """Separate `input_path` with `model`. Returns lower-cased stem name -> wav path inside out_dir."""
    import torch
    from audio_separator.separator import Separator

    tmp = out_dir / f"_tmp_{Path(model).stem}"
    tmp.mkdir(parents=True, exist_ok=True)
    separator = Separator(
        log_level=logging.ERROR,
        model_file_dir=str(MODEL_DIR),
        output_dir=str(tmp),
        output_format="WAV",
        sample_rate=44100,
        use_autocast=torch.cuda.is_available(),
        # Overlap 2 instead of the models' default 4: twice as fast, and on our ground-truth test the
        # vocal SDR moved from 18.18 to 18.13 dB (inaudible). Batching didn't help on GPU.
        mdxc_params={"segment_size": 256, "override_model_segment_size": False, "batch_size": 1,
                     "overlap": 2, "pitch_shift": 0},
    )
    separator.load_model(model_filename=model)
    with _tqdm_progress(progress):
        produced = separator.separate(str(input_path))

    stems: dict[str, Path] = {}
    for name in produced:
        path = Path(name)
        if not path.is_absolute():
            path = tmp / path.name
        match = _STEM_RE.findall(path.stem)
        if not match:
            continue
        stem = match[-1].lower()
        final = out_dir / f"{Path(model).stem}__{stem}.wav"
        shutil.move(str(path), final)
        stems[stem] = final
    shutil.rmtree(tmp, ignore_errors=True)

    del separator
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    if not stems:
        raise RuntimeError(f"{model} produced no output")
    return stems


def pick(stems: dict[str, Path], *candidates: str, exclude: tuple[str, ...] = ()) -> Path:
    """Find a stem by (partial) name, since model stem labels vary ("vocals", "Vocals", "noreverb", ...)."""
    for cand in candidates:
        for name, path in stems.items():
            if cand in name and not any(x in name for x in exclude):
                return path
    raise KeyError(f"none of {candidates} in {list(stems)}")
