"""Small, dependency-free project configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_directory() -> Path:
    """Return the configured data directory without creating it.

    Relative paths are resolved from the repository root. The environment
    variable is intentionally optional so Phase 1 does not require a ``.env``
    loader.
    """

    configured_path = Path(os.getenv("RATING_RELIABILITY_DATA_DIR", "data"))
    if configured_path.is_absolute():
        return configured_path
    return PROJECT_ROOT / configured_path
