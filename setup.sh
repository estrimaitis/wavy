#!/usr/bin/env bash
# One-time setup for macOS / Linux. (Windows: use setup.bat)
set -euo pipefail
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "ffmpeg not found. Install it (brew install ffmpeg / sudo apt install ffmpeg) and re-run."; exit 1; }

PY=""
for cand in python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 12) else 1)'; then
    PY="$cand"; break
  fi
done
[ -n "$PY" ] || { echo "Python 3.11 or 3.12 is required."; exit 1; }

[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null

if command -v nvidia-smi >/dev/null; then
  echo "Installing PyTorch with CUDA 12.8 ..."
  .venv/bin/python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
else
  echo "No NVIDIA GPU found - installing the default PyTorch build (CPU / Apple Silicon). Unbaking will be slower."
  .venv/bin/python -m pip install torch torchaudio
fi
.venv/bin/python -c "import torch,torchaudio;print('torch=='+torch.__version__);print('torchaudio=='+torchaudio.__version__)" > .venv/constraints.txt

echo "Installing the rest ..."
.venv/bin/python -m pip install -c .venv/constraints.txt -r requirements.txt
echo
echo "Done. Run ./start.sh to launch Wavy."
