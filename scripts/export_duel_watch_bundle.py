"""Export a single logged duel into an EDOPro watch bundle.

This creates:
- two .ydk files (exact decklists used by each bot profile for that year)
- duel-meta.json (seed, players, game result)
- WATCH_ME.txt with replay steps for EDOPro
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ygotrainingbot.ydk import write_ydk


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _deck_for_bot(output_dir: Path, year: int, bot_id: str) -> tuple[str, tuple[int, ...]]:
    pack_path = output_dir / str(year) / "packs" / f"{bot_id}.json"
    payload = _load_json(pack_path)
    decks = payload.get("decks", [])
    if not decks:
        raise ValueError(f"No deck in {pack_path}")
    deck = decks[0]
    return str(deck.get("name", bot_id)), tuple(int(card) for card in deck.get("main", []))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-log", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--edopro-deck-dir", type=Path, default=Path("C:/ProjectIgnis/deck"))
    args = parser.parse_args()

    game = _load_json(args.game_log)
    meta = dict(game.get("meta", {}))
    year = int(meta["year"])
    home_bot = str(meta["home_bot_id"])
    away_bot = str(meta["away_bot_id"])
    seed = list(meta.get("duel_seed", []))
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # infer bracket root from /{year}/games/.../game-xx.json
    parts = args.game_log.resolve().parts
    year_idx = parts.index(str(year))
    bracket_root = Path(*parts[:year_idx])

    home_name, home_main = _deck_for_bot(bracket_root, year, home_bot)
    away_name, away_main = _deck_for_bot(bracket_root, year, away_bot)

    home_file = output_dir / f"{home_bot}-{home_name.replace(' ', '_')}.ydk"
    away_file = output_dir / f"{away_bot}-{away_name.replace(' ', '_')}.ydk"
    write_ydk(home_file, home_main, header_lines=[f"# {home_bot} {home_name}", f"# year={year}"])
    write_ydk(away_file, away_main, header_lines=[f"# {away_bot} {away_name}", f"# year={year}"])

    if args.edopro_deck_dir.exists():
        write_ydk(args.edopro_deck_dir / home_file.name, home_main, header_lines=[f"# {home_bot} {home_name}"])
        write_ydk(args.edopro_deck_dir / away_file.name, away_main, header_lines=[f"# {away_bot} {away_name}"])

    watch = {
        "game_log": str(args.game_log.resolve()),
        "year": year,
        "home_bot_id": home_bot,
        "away_bot_id": away_bot,
        "goes_first": meta.get("goes_first"),
        "duel_seed": seed,
        "result": game.get("result", {}),
        "deck_files": [str(home_file.resolve()), str(away_file.resolve())],
    }
    (output_dir / "duel-meta.json").write_text(json.dumps(watch, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "WATCH_ME.txt").write_text(
        "\n".join(
            [
                "EDOPro replay bundle (deterministic setup metadata).",
                "",
                "EDOPro does not expose a stable writer for .yrpX from external engines.",
                "This bundle gives you the exact decks and seed metadata to recreate/watch the duel flow manually.",
                "",
                f"Home deck copied as: {home_file.name}",
                f"Away deck copied as: {away_file.name}",
                f"Goes first: {meta.get('goes_first')}",
                f"Seed: {seed}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(watch, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

