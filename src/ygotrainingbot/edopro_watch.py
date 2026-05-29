"""Export logged duels into EDOPro-friendly watch bundles."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from ygotrainingbot.ydk import write_ydk


def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned or "duel"


def load_game_log(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"game log must be a JSON object: {path}")
    return payload


def bracket_root_from_game_log(game_log: Path, year: int) -> Path:
    parts = game_log.resolve().parts
    year_text = str(year)
    if year_text not in parts:
        raise ValueError(f"game log path must include year {year}: {game_log}")
    year_idx = parts.index(year_text)
    return Path(*parts[:year_idx])


def deck_for_bot(bracket_root: Path, year: int, bot_id: str) -> tuple[str, tuple[int, ...]]:
    pack_path = bracket_root / str(year) / "packs" / f"{bot_id}.json"
    payload = json.loads(pack_path.read_text(encoding="utf-8"))
    decks = payload.get("decks", [])
    if not decks:
        raise ValueError(f"No deck in {pack_path}")
    deck = decks[0]
    return str(deck.get("name", bot_id)), tuple(int(card) for card in deck.get("main", []))


def list_game_logs(bracket_root: Path, year: int) -> list[Path]:
    games_dir = bracket_root / str(year) / "games"
    if not games_dir.is_dir():
        return []
    return sorted(games_dir.glob("**/game-*.json"))


def pick_random_game_logs(
    bracket_root: Path,
    *,
    year: int,
    count: int = 2,
    rng: random.Random | None = None,
) -> list[Path]:
    logs = list_game_logs(bracket_root, year)
    if not logs:
        raise FileNotFoundError(f"No game logs under {bracket_root / str(year) / 'games'}")
    picker = rng or random.Random()
    return picker.sample(logs, k=min(count, len(logs)))


def _timeline_entries(game: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, trace in enumerate(game.get("traces", []), start=1):
        if not isinstance(trace, dict):
            continue
        state = trace.get("state")
        action = trace.get("action")
        if isinstance(state, dict) and isinstance(action, dict):
            turn = state.get("turn")
            summary = state.get("summary")
            action_id = action.get("action_id")
            label = action.get("label")
            tags = list(action.get("tags", ()))
            agent = trace.get("agent_name", trace.get("agent"))
        else:
            turn = trace.get("turn")
            summary = trace.get("summary")
            action_id = trace.get("selected_action")
            label = trace.get("selected_label")
            tags = list(trace.get("selected_tags", ()))
            agent = trace.get("agent", trace.get("agent_name"))
        entries.append(
            {
                "step": index,
                "turn": turn,
                "agent": agent,
                "summary": summary,
                "action_id": action_id,
                "label": label,
                "tags": tags,
            }
        )
    return entries


def _format_timeline_line(entry: dict[str, Any]) -> str:
    label = str(entry.get("label") or entry.get("action_id") or "unknown action")
    if len(label) > 96:
        label = label[:93] + "..."
    tags = entry.get("tags") or []
    tag_text = f" [{', '.join(str(tag) for tag in tags[:4])}]" if tags else ""
    return (
        f"{entry['step']:4d} | turn {entry.get('turn')} | {entry.get('agent')} | "
        f"{label}{tag_text}"
    )


def export_edopro_watch_bundle(
    game_log: Path,
    output_dir: Path,
    *,
    edopro_deck_dir: Path | None = None,
    bundle_name: str | None = None,
) -> dict[str, Any]:
    """Write decks, timeline, and EDOPro open instructions for one logged duel."""

    game = load_game_log(game_log)
    meta = dict(game.get("meta", {}))
    year = int(meta["year"])
    home_bot = str(meta["home_bot_id"])
    away_bot = str(meta["away_bot_id"])
    home_name = str(meta.get("home_name", home_bot))
    away_name = str(meta.get("away_name", away_bot))
    bracket_root = bracket_root_from_game_log(game_log, year)

    label = bundle_name or slug(f"{home_name}-vs-{away_name}-game-{meta.get('game_number', 0)}")
    bundle_dir = output_dir / label
    bundle_dir.mkdir(parents=True, exist_ok=True)

    home_deck_name, home_main = deck_for_bot(bracket_root, year, home_bot)
    away_deck_name, away_main = deck_for_bot(bracket_root, year, away_bot)

    home_ydk = bundle_dir / f"{slug(home_name)}.ydk"
    away_ydk = bundle_dir / f"{slug(away_name)}.ydk"
    write_ydk(
        home_ydk,
        home_main,
        header_lines=[
            f"# {home_name} ({home_bot})",
            f"# {home_deck_name}",
            f"# year={year}",
        ],
    )
    write_ydk(
        away_ydk,
        away_main,
        header_lines=[
            f"# {away_name} ({away_bot})",
            f"# {away_deck_name}",
            f"# year={year}",
        ],
    )

    if edopro_deck_dir is not None:
        edopro_deck_dir.mkdir(parents=True, exist_ok=True)
        write_ydk(edopro_deck_dir / home_ydk.name, home_main, header_lines=[f"# {home_name}"])
        write_ydk(edopro_deck_dir / away_ydk.name, away_main, header_lines=[f"# {away_name}"])

    result = dict(game.get("result", {}))
    timeline = _timeline_entries(game)
    watch = {
        "bundle": str(bundle_dir.resolve()),
        "game_log": str(game_log.resolve()),
        "year": year,
        "home_bot_id": home_bot,
        "away_bot_id": away_bot,
        "home_name": home_name,
        "away_name": away_name,
        "goes_first": meta.get("goes_first"),
        "duel_seed": list(meta.get("duel_seed", [])),
        "result": result,
        "decisions": len(timeline),
        "deck_files": [str(home_ydk.resolve()), str(away_ydk.resolve())],
    }
    (bundle_dir / "duel-meta.json").write_text(json.dumps(watch, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (bundle_dir / "action-timeline.json").write_text(
        json.dumps(timeline, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    timeline_lines = [_format_timeline_line(entry) for entry in timeline[:500]]
    if len(timeline) > 500:
        timeline_lines.append(f"... ({len(timeline) - 500} more steps in action-timeline.json)")
    (bundle_dir / "action-timeline.txt").write_text("\n".join(timeline_lines) + "\n", encoding="utf-8")

    winner = result.get("winner")
    end_reason = result.get("end_reason", "unknown")
    goes_first_id = str(meta.get("goes_first", ""))
    goes_first_name = away_name if goes_first_id == away_bot else home_name if goes_first_id == home_bot else goes_first_id
    winner_name = home_name if winner == home_bot else away_name if winner == away_bot else str(winner)
    (bundle_dir / "OPEN_IN_EDOPRO.txt").write_text(
        "\n".join(
            [
                "EDOPro watch bundle",
                "===================",
                "",
                f"Match: {home_name} vs {away_name} ({year} bracket)",
                f"Winner: {winner_name} ({end_reason})",
                f"Goes first: {goes_first_name}",
                f"Decisions logged: {len(timeline)}",
                "",
                "Deck files (copy both into your EDOPro deck folder):",
                f"  - {home_ydk.name}  ({home_name}: {home_deck_name})",
                f"  - {away_ydk.name}  ({away_name}: {away_deck_name})",
                "",
                "Watch in EDOPro:",
                "  1. Open EDOPro (Project Ignis).",
                "  2. Deck → Load Deck → load both .ydk files from this folder.",
                "  3. Single → Host / AI duel (Edison 2010 banlist if available).",
                f"  4. Player 1 deck: {home_ydk.name}",
                f"  5. Player 2 deck: {away_ydk.name}",
                f"  6. First player should match: {goes_first_name}",
                "",
                "Replay note:",
                "  Headless training duels do not produce .yrpX files yet.",
                "  Use action-timeline.txt/json in this folder to follow the exact bot line.",
                "  To record a native EDOPro replay, replay the same decks in-client and save replay.",
                "",
                f"Training seed (for developers): {meta.get('duel_seed')}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return watch


def export_random_watch_bundles(
    bracket_root: Path,
    output_dir: Path,
    *,
    year: int = 2010,
    count: int = 2,
    edopro_deck_dir: Path | None = None,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    picks = pick_random_game_logs(bracket_root, year=year, count=count, rng=rng)
    manifest: list[dict[str, Any]] = []
    for game_log in picks:
        manifest.append(
            export_edopro_watch_bundle(
                game_log,
                output_dir,
                edopro_deck_dir=edopro_deck_dir,
            )
        )
    summary = {
        "bracket_root": str(bracket_root.resolve()),
        "year": year,
        "count": len(manifest),
        "seed": seed,
        "bundles": manifest,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "watch-manifest.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
