import json
from pathlib import Path

import pytest

from ygotrainingbot.format_training import load_format_training_config


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
