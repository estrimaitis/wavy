"""Score a pipeline run against the ground-truth stems written by make_test_song.py.

    python tools/evaluate.py <job_dir> <ground_truth_dir>

Prints SDR (signal-to-distortion ratio in dB; higher is better, >10 dB is clean, <0 is poor)
for every ingredient, matching singers to whichever true voice they fit best.
"""
import itertools
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def load(path, n=None):
    x, _ = sf.read(str(path), dtype="float64", always_2d=True)
    if n is not None:
        x = x[:n] if len(x) >= n else np.pad(x, ((0, n - len(x)), (0, 0)))
    return x


def sdr(ref, est):
    return 10 * np.log10((np.sum(ref ** 2) + 1e-12) / (np.sum((ref - est) ** 2) + 1e-12))


def main(job_dir, truth_dir):
    stems_dir = Path(job_dir) / "stems"
    truth = {p.stem: load(p) for p in Path(truth_dir).glob("*.wav")}
    n = len(next(iter(truth.values())))
    est = {p.stem: load(p, n) for p in stems_dir.glob("*.wav") if p.stem != "original"}
    zero = np.zeros((n, 2))

    def total(*names):
        return sum((est[k] for k in names if k in est), zero)

    singers = sorted(k for k in est if k.startswith("singer_"))
    voices = [v for v in ("male", "female") if v in truth]
    scores = {}
    if singers and voices:
        best, best_map = -1e9, None
        for perm in itertools.permutations(singers, min(len(singers), len(voices))):
            s = sum(sdr(truth[v], est[p]) for v, p in zip(voices, perm))
            if s > best:
                best, best_map = s, dict(zip(voices, perm))
        for v, p in best_map.items():
            scores[f"{v} voice  <- {p}"] = sdr(truth[v], est[p])
    scores["all vocals (dry)  <- singers+backing"] = sdr(truth["male"] + truth["female"], total(*singers, "backing"))
    scores["vocal reverb      <- reverb"] = sdr(truth["reverb"], total("reverb"))
    scores["all vocals (wet)  <- singers+backing+reverb"] = sdr(
        truth["male"] + truth["female"] + truth["reverb"], total(*singers, "backing", "reverb"))
    scores["drums             <- drum stems"] = sdr(truth["drums"], total("kick", "snare", "toms", "hihat", "ride", "crash", "drums", "drums_rest"))
    scores["bass              <- bass"] = sdr(truth["bass"], total("bass"))
    scores["synth pad         <- other+piano+guitar"] = sdr(truth["pad"], total("other", "piano", "guitar"))
    for k, v in scores.items():
        print(f"  {k:<44} {v:6.1f} dB")
    return scores


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
