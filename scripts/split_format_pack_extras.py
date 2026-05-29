#!/usr/bin/env python3
"""Split extra-deck monsters out of format pack main lists using cards.cdb."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from ygotrainingbot.deck_lists import is_extra_deck_monster, split_main_and_extra


def _card_type_reader(db_path: Path):
    conn = sqlite3.connect(db_path)

    def lookup(card_id: int) -> int:
        row = conn.execute("SELECT type FROM datas WHERE id = ?", (card_id,)).fetchone()
        return int(row[0]) if row else 0

    return lookup


def split_pack(pack_path: Path, *, db_path: Path, write: bool) -> dict[str, object]:
    payload = json.loads(pack_path.read_text(encoding="utf-8"))
    lookup = _card_type_reader(db_path)
    changes: list[dict[str, object]] = []

    for deck in payload.get("decks", []):
        if not isinstance(deck, dict):
            continue
        main_ids = deck.get("main", [])
        if not isinstance(main_ids, list):
            continue
        existing_extra = list(deck.get("extra") or [])
        zones = split_main_and_extra(main_ids, card_type=lookup)
        merged_extra = existing_extra + list(zones.extra)
        deck["main"] = list(zones.main)
        deck["extra"] = merged_extra
        changes.append(
            {
                "name": deck.get("name"),
                "main": len(zones.main),
                "extra": len(merged_extra),
                "moved_to_extra": len(zones.extra),
            }
        )

    if write:
        pack_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {"pack": str(pack_path), "decks": changes, "written": write}


def _resolve_db(edopro_home: Path) -> Path:
    for candidate in (edopro_home / "cards.cdb", edopro_home / "expansions" / "cards.cdb"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"No cards.cdb found under {edopro_home}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packs", nargs="+", type=Path)
    parser.add_argument("--edopro-home", type=Path, default=Path(".ygotrain/edopro-home"))
    parser.add_argument("--write", action="store_true", help="Rewrite pack JSON files in place.")
    args = parser.parse_args()

    db_path = _resolve_db(args.edopro_home)
    reports = [split_pack(path, db_path=db_path, write=args.write) for path in args.packs]
    print(json.dumps({"database": str(db_path), "reports": reports}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
