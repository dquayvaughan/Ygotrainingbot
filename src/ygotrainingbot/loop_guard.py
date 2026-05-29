"""Disk safety helpers for long training loops (P2)."""

from __future__ import annotations

import shutil
from pathlib import Path


def free_gigabytes(path: Path) -> float:
    usage = shutil.disk_usage(path.resolve())
    return usage.free / (1024**3)


def ensure_disk_headroom(path: Path, *, min_free_gb: float = 1.0) -> None:
    free_gb = free_gigabytes(path)
    if free_gb < min_free_gb:
        raise RuntimeError(
            f"Insufficient disk space at {path}: {free_gb:.2f} GB free "
            f"(minimum required {min_free_gb:.2f} GB)."
        )
