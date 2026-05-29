#!/usr/bin/env python3
"""Export yearly bracket deck lists as .ydk files for EDOPro review."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from ygotrainingbot.league_tournament import YEARLY_ARCHETYPE_BY_BOT, resolve_year_deck
from ygotrainingbot.ydk import export_manifest_entry, write_manifest, write_ydk

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROSTER = ROOT / "configs/league-rosters/progression-ycs-regionals.json"


def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned or "deck"


def export_year(
    *,
    year: int,
    roster_path: Path,
    output_dir: Path,
    edopro_deck_dir: Path | None,
) -> Path:
    payload = json.loads(roster_path.read_text(encoding="utf-8"))
    bots = payload.get("bots", [])
    if not isinstance(bots, list):
        raise ValueError(f"roster at {roster_path} must include a bots list.")

    review_dir = output_dir / "decks-review" / str(year)
    review_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []

    for profile in bots:
        if not isinstance(profile, dict):
            continue
        bot_id = str(profile["bot_id"])
        bot_name = str(profile.get("name", bot_id))
        archetype = YEARLY_ARCHETYPE_BY_BOT.get(year, {}).get(bot_id, "unknown")
        pack_path, deck, resolved_archetype = resolve_year_deck(bot_id, year, dict(profile))

        filename = f"{year}-{bot_id}-{slug(bot_name)}-{slug(resolved_archetype)}.ydk"
        ydk_path = review_dir / filename
        header = [
            f"#ygotrainingbot year={year} bot={bot_id} ({bot_name})",
            f"#archetype: {resolved_archetype}",
            f"#shell: {deck.name}",
            f"#pack: {pack_path.as_posix()}",
        ]
        write_ydk(ydk_path, deck.main, header_lines=header)

        if edopro_deck_dir is not None:
            edopro_deck_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ydk_path, edopro_deck_dir / filename)

        entries.append(
            export_manifest_entry(
                bot_id=bot_id,
                bot_name=bot_name,
                year=year,
                archetype=resolved_archetype,
                pack_path=pack_path,
                deck_name=deck.name,
                ydk_path=ydk_path,
                main_count=len(deck.main),
            )
        )

    manifest_path = review_dir / "manifest.json"
    write_manifest(manifest_path, entries)

    readme = review_dir / "README.txt"
    readme.write_text(
        "\n".join(
            [
                f"YGOTrainingBot — {year} bracket decks for EDOPro review",
                "",
                "Open EDOPro → Deck → Load, or copy these into your deck folder:",
                f"  {edopro_deck_dir}" if edopro_deck_dir else "  (not copied to EDOPro)",
                "",
                "Each file matches the shell used in run-yearly-bracket for that bot/year.",
                "See manifest.json for bot_id, archetype label, and source pack.",
                "",
                "Files:",
                *[f"  - {entry['ydk_file']}" for entry in entries],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2010)
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/yearly-bracket-2010",
        help="Bracket output directory (writes <output-dir>/decks-review/<year>/).",
    )
    parser.add_argument(
        "--edopro-deck-dir",
        type=Path,
        default=Path("C:/ProjectIgnis/deck"),
        help="Also copy .ydk files here for in-client review (set empty to skip).",
    )
    args = parser.parse_args()
    edopro_dir = args.edopro_deck_dir if str(args.edopro_deck_dir) else None
    manifest = export_year(
        year=args.year,
        roster_path=args.roster,
        output_dir=args.output_dir,
        edopro_deck_dir=edopro_dir,
    )
    print(f"Wrote {manifest.parent}")
    print(f"Manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
