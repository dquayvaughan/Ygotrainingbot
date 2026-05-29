import json
from pathlib import Path

from ygotrainingbot.deck_lists import DeckZones
from ygotrainingbot.format_matrix import duel_mode_for_pack, run_format_matrix
from ygotrainingbot.format_training import FormatBanlist, FormatPack, load_format_pack


def test_duel_mode_for_pack() -> None:
    banlist = FormatBanlist()
    goat = FormatPack(name="goat-2005", decks=(), banlist=banlist)
    edison = FormatPack(name="edison-2010", decks=(), banlist=banlist)
    assert duel_mode_for_pack(goat) == "goat"
    assert duel_mode_for_pack(edison) == "mr3"
    assert duel_mode_for_pack(FormatPack(name="unknown", decks=(), banlist=goat.banlist)) == "mr3"


def test_run_format_matrix_aggregates_matchups(tmp_path: Path) -> None:
    pack_path = tmp_path / "mini.json"
    pack_path.write_text(
        json.dumps(
            {
                "name": "mini",
                "banlist": {},
                "decks": [
                    {"name": "a", "main": [1] * 40, "extra": [2, 3]},
                    {"name": "b", "main": [4] * 40},
                ],
            }
        ),
        encoding="utf-8",
    )

    calls: list[dict[str, object]] = []

    def play_duel(_gateway: str, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        first = str(kwargs["first_agent"])
        return {
            "wins_by_agent": {first: 1},
            "script_stats": {"runtime_errors": 0},
            "tags": {"special-summon": 1},
        }

    report = run_format_matrix(
        packs=[pack_path],
        play_duel=play_duel,
        gateway_command_for_mode=lambda mode: f"gateway-{mode}",
        games_per_matchup=1,
        policy="search-control",
    )

    assert report["passed"] is True
    assert len(report["formats"]) == 1
    assert report["formats"][0]["duel_mode"] == "mr3"
    assert len(report["formats"][0]["matchups"]) == 4
    assert calls[0]["first_deck"] == DeckZones(main=tuple([1] * 40), extra=(2, 3))


def test_edison_pack_loads_extra_decks() -> None:
    pack = load_format_pack(Path("configs/format-packs/edison-2010.json"))
    quickdraw = next(deck for deck in pack.decks if "Quickdraw" in deck.name)
    frog = next(deck for deck in pack.decks if "Frog" in deck.name)
    assert len(quickdraw.extra) >= 8
    assert len(frog.extra) >= 6
