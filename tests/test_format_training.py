import json
from pathlib import Path

import pytest

from ygotrainingbot.format_training import load_format_pack, load_format_training_config


def test_load_format_training_config(tmp_path: Path) -> None:
    config_path = tmp_path / "format.json"
    config_path.write_text(
        json.dumps(
            {
                "name": "test-format",
                "description": "fixture",
                "games": 3,
                "max_decisions": 12,
                "deck_a": [1184620] * 40,
                "deck_b": [3134241] * 40,
            }
        ),
        encoding="utf-8",
    )

    config = load_format_training_config(config_path)

    assert config.name == "test-format"
    assert config.games == 3
    assert config.max_decisions == 12
    assert len(config.deck_a) == 40
    assert len(config.deck_b) == 40


def test_format_training_config_rejects_short_decks(tmp_path: Path) -> None:
    config_path = tmp_path / "format.json"
    config_path.write_text(
        json.dumps(
            {
                "name": "bad-format",
                "deck_a": [1184620],
                "deck_b": [3134241] * 40,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="deck_a"):
        load_format_training_config(config_path)


def test_load_format_pack_with_banlist_and_decks(tmp_path: Path) -> None:
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(
        json.dumps(
            {
                "name": "mini-pack",
                "games": 2,
                "max_decisions": 8,
                "banlist": {
                    "forbidden": [1],
                    "limited": [2],
                    "semi_limited": [3],
                },
                "decks": [
                    {
                        "name": "deck one",
                        "archetype": "test",
                        "source": "fixture",
                        "main": [1184620] * 40,
                    },
                    {
                        "name": "deck two",
                        "main": [3134241] * 40,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    pack = load_format_pack(pack_path)

    assert pack.name == "mini-pack"
    assert pack.banlist.limit_for(1) == 0
    assert pack.banlist.limit_for(2) == 1
    assert pack.banlist.limit_for(3) == 2
    assert pack.banlist.limit_for(4) == 3
    assert [deck.name for deck in pack.decks] == ["deck one", "deck two"]
    assert pack.max_duel_turns == 0


def test_load_format_pack_honors_max_duel_turns_override(tmp_path: Path) -> None:
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(
        json.dumps(
            {
                "name": "fast-pack",
                "max_duel_turns": 12,
                "decks": [{"name": "deck one", "main": [1184620] * 40}],
            }
        ),
        encoding="utf-8",
    )

    pack = load_format_pack(pack_path)

    assert pack.max_duel_turns == 12


def test_repository_format_packs_load() -> None:
    pack_paths = sorted(Path("configs/format-packs").glob("*.json"))

    assert pack_paths
    for pack_path in pack_paths:
        pack = load_format_pack(pack_path)
        assert pack.decks
        assert all(len(deck.main) >= 40 for deck in pack.decks)
        assert all(len(deck.extra) <= 15 for deck in pack.decks)
