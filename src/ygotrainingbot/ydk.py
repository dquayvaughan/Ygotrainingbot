"""Write EDOPro-compatible .ydk deck files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence


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
