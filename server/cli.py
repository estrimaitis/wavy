"""Run the pipeline without the web UI:  python -m server.cli song.mp3 [out_dir]"""
import sys
import time
from pathlib import Path

from . import env  # noqa: F401  (sets model cache locations)
from . import pipeline


def main():
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data") / "cli" / src.stem
    t0 = time.time()

    def report(stage, frac):
        print(f"[{time.time() - t0:6.1f}s] {stage}", flush=True)

    result = pipeline.run(out, src, {}, report)
    print(f"\nDone in {time.time() - t0:.1f}s -> {out}")
    for t in result["tracks"]:
        print(f"  {t['label']:<22} {t['rms_db']:6.1f} dB {'(empty)' if t['empty'] else ''}  {t['note']}")
    for line in result["lyrics"]["lines"]:
        print(f"  {line['start']:6.2f} [{line.get('singer', 0)}] {line['text']}")
    print("timings:", result["timings"])


if __name__ == "__main__":
    main()
