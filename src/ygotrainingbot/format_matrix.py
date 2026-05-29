"""Cross-format smoke testing for gateway + deck complexity."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Callable

from ygotrainingbot.deck_lists import deck_zones_from_format_deck
from ygotrainingbot.format_training import FormatDeck, FormatPack, load_format_pack
from ygotrainingbot.league_tournament import generate_duel_seed

PlayDuelFn = Callable[..., dict[str, Any]]

DEFAULT_DUEL_MODE_BY_PACK = {
    "goat-2005": "goat",
    "edison-2010": "mr3",
    "proof-normal-baseline": "mr3",
}


def duel_mode_for_pack(pack: FormatPack) -> str:
    return DEFAULT_DUEL_MODE_BY_PACK.get(pack.name, "mr3")


def run_format_matrix(
    *,
    packs: list[Path],
    play_duel: PlayDuelFn,
    gateway_command_for_mode: Callable[[str], str],
    games_per_matchup: int = 2,
    policy: str = "search-control",
    timeout_seconds: float = 120.0,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Run representative duels for every deck pair in each format pack."""

    randomizer = rng or random.Random()
    formats: list[dict[str, Any]] = []

    for pack_path in packs:
        pack = load_format_pack(pack_path)
        duel_mode = duel_mode_for_pack(pack)
        gateway = gateway_command_for_mode(duel_mode)
        matchups: list[dict[str, Any]] = []

        for deck_a in pack.decks:
            for deck_b in pack.decks:
                zones_a = deck_zones_from_format_deck(deck_a)
                zones_b = deck_zones_from_format_deck(deck_b)
                wins_a = 0
                wins_b = 0
                draws = 0
                runtime_errors = 0
                extra_summons = 0
                game_reports: list[dict[str, Any]] = []

                for game_number in range(1, games_per_matchup + 1):
                    first_is_a = game_number % 2 == 1
                    first_deck, second_deck = (deck_a, deck_b) if first_is_a else (deck_b, deck_a)
                    first_zones, second_zones = (zones_a, zones_b) if first_is_a else (zones_b, zones_a)
                    first_label = "deck-a" if first_is_a else "deck-b"
                    second_label = "deck-b" if first_is_a else "deck-a"
                    seed = generate_duel_seed(randomizer)
                    report = play_duel(
                        gateway,
                        first_agent=first_label,
                        second_agent=second_label,
                        first_policy=policy,
                        second_policy=policy,
                        first_weights=None,
                        second_weights=None,
                        first_deck=first_zones,
                        second_deck=second_zones,
                        seed=seed,
                        timeout_seconds=timeout_seconds,
                        format_name=pack.name,
                        game_number=game_number,
                        goes_first=first_label,
                    )
                    game_reports.append(report)
                    wins = dict(report.get("wins_by_agent", {}))
                    if int(wins.get("deck-a", 0)) > 0:
                        wins_a += 1
                    elif int(wins.get("deck-b", 0)) > 0:
                        wins_b += 1
                    else:
                        draws += 1
                    script_stats = dict(report.get("script_stats", {}))
                    runtime_errors += int(script_stats.get("runtime_errors", 0) or 0)
                    tags = dict(report.get("tags", {}))
                    extra_summons += int(tags.get("special-summon", 0) or 0)

                matchups.append(
                    {
                        "deck_a": deck_a.name,
                        "deck_b": deck_b.name,
                        "deck_a_extra_size": len(zones_a.extra),
                        "deck_b_extra_size": len(zones_b.extra),
                        "games": games_per_matchup,
                        "deck_a_wins": wins_a,
                        "deck_b_wins": wins_b,
                        "draws": draws,
                        "runtime_errors": runtime_errors,
                        "special_summon_tags": extra_summons,
                        "passed": runtime_errors == 0 and draws < games_per_matchup,
                        "reports": game_reports,
                    }
                )

        formats.append(
            {
                "pack": str(pack_path.resolve()),
                "name": pack.name,
                "duel_mode": duel_mode,
                "decks": [
                    {
                        "name": deck.name,
                        "main": len(deck.main),
                        "extra": len(deck.extra),
                    }
                    for deck in pack.decks
                ],
                "matchups": matchups,
                "passed": all(item["passed"] for item in matchups),
            }
        )

    return {
        "formats": formats,
        "passed": all(item["passed"] for item in formats),
    }
