"""lab/config.py — environment helpers for the science core.

Single place that reads MAT_PROJECT_API. Always pass api_key= explicitly;
never rely on mp-api's own MP_API_KEY env-var lookup.
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from .env without overwriting existing env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_dotenv(_ROOT / ".env")


def get_api_key() -> str:
    """Return the Materials Project API key (empty string if not set)."""
    return os.environ.get("MAT_PROJECT_API", "").strip()
