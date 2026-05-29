"""Write EDOPro-compatible .ydk deck files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence


def read_ydk(path: Path) -> dict[str, tuple[int, ...]]:
    """Parse an EDOPro .ydk deck file into main, extra, and side card ID lists."""

    section: str | None = None
    zones: dict[str, list[int]] = {"main": [], "extra": [], "side": []}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            lowered = line.lower()
            if lowered.startswith("#main"):
                section = "main"
            elif lowered.startswith("#extra"):
                section = "extra"
            elif lowered.startswith("#side"):
                section = "side"
            continue
        if section is None:
            continue
        try:
            zones[section].append(int(line))
        except ValueError as exc:
            raise ValueError(f"invalid card id in {path}: {line!r}") from exc
    if len(zones["main"]) < 40:
        raise ValueError(f"{path} main deck must contain at least 40 cards (got {len(zones['main'])}).")
    if len(zones["main"]) > 60:
        raise ValueError(f"{path} main deck may contain at most 60 cards (got {len(zones['main'])}).")
    if len(zones["extra"]) > 15:
        raise ValueError(f"{path} extra deck may contain at most 15 cards.")
    if len(zones["side"]) > 15:
        raise ValueError(f"{path} side deck may contain at most 15 cards.")
    return {
        "main": tuple(zones["main"]),
        "extra": tuple(zones["extra"]),
        "side": tuple(zones["side"]),
    }


def write_ydk(
    path: Path,
    main: Sequence[int],
    *,
    extra: Sequence[int] = (),
    side: Sequence[int] = (),
    header_lines: Sequence[str] = (),
) -> Path:
    """Write a .ydk file (main / extra / side sections)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["#created by ygotrainingbot"]
    lines.extend(header_lines)
    lines.append("#main")
    lines.extend(str(card_id) for card_id in main)
    lines.append("#extra")
    lines.extend(str(card_id) for card_id in extra)
    lines.append("#side")
    lines.extend(str(card_id) for card_id in side)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def export_manifest_entry(
    *,
    bot_id: str,
    bot_name: str,
    year: int,
    archetype: str,
    pack_path: Path,
    deck_name: str,
    ydk_path: Path,
    main_count: int,
) -> dict[str, object]:
    return {
        "bot_id": bot_id,
        "bot_name": bot_name,
        "year": year,
        "archetype": archetype,
        "pack": str(pack_path),
        "deck_shell": deck_name,
        "ydk_file": str(ydk_path),
        "main_deck_size": main_count,
    }


def write_manifest(path: Path, entries: Sequence[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"decks": list(entries)}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
