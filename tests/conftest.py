import os
import subprocess
import sys
from pathlib import Path

# Isolate tests from any developer config / real DB / real API key, before importing the app.
os.environ["GUARDIAN_HASH_SALT"] = "test-salt"
os.environ["GUARDIAN_DB_URL"] = "sqlite://"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["GUARDIAN_ENGINE"] = "baseline"
os.environ["GUARDIAN_DASHBOARD_PASSWORD"] = ""

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402


def _ensure_model() -> None:
    """Pipeline tests need the baseline model; build data + model once if missing."""
    if Path(get_settings().model_path).exists():
        return
    if not (ROOT / "data" / "synthetic" / "train.jsonl").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_synthetic.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "train_baseline.py"), "--no-pan"], check=True)


_ensure_model()


@pytest.fixture
def anyio_backend():
    return "asyncio"
