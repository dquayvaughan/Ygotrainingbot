"""Chronological format progression (Phase 7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence


DEFAULT_CHRONOLOGY: tuple[tuple[str, str], ...] = (
    ("2005", "configs/format-packs/goat-2005.json"),
    ("2010", "configs/format-packs/edison-2010.json"),
)


def chronological_pack_paths(repo_root: Path | None = None) -> list[Path]:
    root = repo_root or Path.cwd()
    return [root / relative for _label, relative in DEFAULT_CHRONOLOGY]


def build_chronological_plan(
    *,
    repo_root: Path | None = None,
    packs: Sequence[Path] | None = None,
    roster_path: Path | None = None,
    bracket_years: Sequence[int] | None = None,
) -> dict[str, Any]:
    root = repo_root or Path.cwd()
    resolved_packs = list(packs) if packs else chronological_pack_paths(root)
    plan: dict[str, Any] = {
        "formats": [{"pack": str(path.resolve()), "label": path.stem} for path in resolved_packs],
        "roster": str(roster_path.resolve()) if roster_path else None,
        "bracket_years": list(bracket_years or []),
    }
    return plan


def write_chronological_plan(path: Path, plan: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
