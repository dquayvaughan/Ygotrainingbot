"""Configuration helpers for format-specific gameplay training."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class FormatTrainingConfig:
    """Deck and run defaults for training one Yu-Gi-Oh! format."""

    name: str
    deck_a: tuple[int, ...]
    deck_b: tuple[int, ...]
    description: str = ""
    games: int = 25
    max_decisions: int = 40

    def validate(self) -> None:
        if not self.name:
            raise ValueError("format config requires a name.")
        if len(self.deck_a) < 40:
            raise ValueError("deck_a must contain at least 40 card IDs.")
        if len(self.deck_b) < 40:
            raise ValueError("deck_b must contain at least 40 card IDs.")
        if self.games < 1:
            raise ValueError("games must be at least 1.")
        if self.max_decisions < 1:
            raise ValueError("max_decisions must be at least 1.")


def load_format_training_config(path: Path) -> FormatTrainingConfig:
    """Load a JSON format training config from disk."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Format config {path} must contain a JSON object.")

    config = FormatTrainingConfig(
        name=str(payload.get("name", "")).strip(),
        description=str(payload.get("description", "")),
        deck_a=_card_ids(payload.get("deck_a"), "deck_a"),
        deck_b=_card_ids(payload.get("deck_b"), "deck_b"),
        games=int(payload.get("games", 25)),
        max_decisions=int(payload.get("max_decisions", 40)),
    )
    config.validate()
    return config


def _card_ids(value: Any, field_name: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of card IDs.")
    try:
        return tuple(int(card_id) for card_id in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} contains a non-integer card ID.") from exc
