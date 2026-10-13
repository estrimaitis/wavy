"""Keep every downloaded model inside the project's models/ folder. Import before anything ML."""
import os
from pathlib import Path

MODELS = Path(__file__).resolve().parent.parent / "models"
MODELS.mkdir(exist_ok=True)
os.environ.setdefault("HF_HOME", str(MODELS / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TORCH_HOME", str(MODELS / "torch"))
