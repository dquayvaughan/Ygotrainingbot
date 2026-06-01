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
        if self.max_decisions < 0:
            raise ValueError("max_decisions must be 0 (unlimited) or greater.")


@dataclass(frozen=True, slots=True)
class FormatDeck:
    """A named deck list inside a format pack."""

    name: str
    main: tuple[int, ...]
    extra: tuple[int, ...] = ()
    side: tuple[int, ...] = ()
    source: str = ""
    archetype: str = ""

    def validate(self) -> None:
        if not self.name:
            raise ValueError("format deck requires a name.")
        if len(self.main) < 40:
            raise ValueError(f"deck {self.name!r} must contain at least 40 main deck card IDs.")
        if len(self.main) > 60:
            raise ValueError(f"deck {self.name!r} main deck may contain at most 60 cards.")
        if len(self.extra) > 15:
            raise ValueError(f"deck {self.name!r} extra deck may contain at most 15 cards.")
        if len(self.side) > 15:
            raise ValueError(f"deck {self.name!r} side deck may contain at most 15 cards.")


@dataclass(frozen=True, slots=True)
class FormatBanlist:
    """Card limit metadata for a format."""

    forbidden: tuple[int, ...] = ()
    limited: tuple[int, ...] = ()
    semi_limited: tuple[int, ...] = ()

    def limit_for(self, card_id: int) -> int:
        if card_id in self.forbidden:
            return 0
        if card_id in self.limited:
            return 1
        if card_id in self.semi_limited:
            return 2
        return 3


@dataclass(frozen=True, slots=True)
class FormatPack:
    """A trainable format with banlist metadata and multiple deck lists."""

    name: str
    decks: tuple[FormatDeck, ...]
    banlist: FormatBanlist
    description: str = ""
    games: int = 25
    max_decisions: int = 40
    max_duel_turns: int = 0

    def validate(self) -> None:
        if not self.name:
            raise ValueError("format pack requires a name.")
        if len(self.decks) < 1:
            raise ValueError("format pack must include at least one deck.")
        for deck in self.decks:
            deck.validate()
        if self.games < 1:
            raise ValueError("games must be at least 1.")
        if self.max_decisions < 0:
            raise ValueError("max_decisions must be 0 (unlimited) or greater.")
        if self.max_duel_turns < 0:
            raise ValueError("max_duel_turns must be 0 or greater.")


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


def load_format_pack(path: Path) -> FormatPack:
    """Load a format pack containing banlist metadata and multiple decks."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Format pack {path} must contain a JSON object.")

    decks_payload = payload.get("decks")
    if not isinstance(decks_payload, list):
        raise ValueError("format pack must include a decks list.")

    banlist_payload = payload.get("banlist", {})
    if not isinstance(banlist_payload, dict):
        raise ValueError("banlist must be an object.")

    pack = FormatPack(
        name=str(payload.get("name", "")).strip(),
        description=str(payload.get("description", "")),
        games=int(payload.get("games", 25)),
        max_decisions=int(payload.get("max_decisions", 40)),
        max_duel_turns=int(payload.get("max_duel_turns", 0)),
        banlist=FormatBanlist(
            forbidden=_card_ids_or_empty(banlist_payload.get("forbidden"), "banlist.forbidden"),
            limited=_card_ids_or_empty(banlist_payload.get("limited"), "banlist.limited"),
            semi_limited=_card_ids_or_empty(
                banlist_payload.get("semi_limited"),
                "banlist.semi_limited",
            ),
        ),
        decks=tuple(_format_deck(deck_payload) for deck_payload in decks_payload),
    )
    pack.validate()
    return pack


def _card_ids(value: Any, field_name: str) -> tuple[int, ...]:
    from ygotrainingbot.card_ids import canonicalize_card_ids

    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of card IDs.")
    try:
        return canonicalize_card_ids(int(card_id) for card_id in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} contains a non-integer card ID.") from exc


def _card_ids_or_empty(value: Any, field_name: str) -> tuple[int, ...]:
    if value is None:
        return ()
    return _card_ids(value, field_name)


def _format_deck(payload: Any) -> FormatDeck:
    if not isinstance(payload, dict):
        raise ValueError("each deck must be an object.")
    return FormatDeck(
        name=str(payload.get("name", "")).strip(),
        source=str(payload.get("source", "")),
        archetype=str(payload.get("archetype", "")),
        main=_card_ids(payload.get("main"), "deck.main"),
        extra=_card_ids_or_empty(payload.get("extra"), "deck.extra"),
        side=_card_ids_or_empty(payload.get("side"), "deck.side"),
    )
