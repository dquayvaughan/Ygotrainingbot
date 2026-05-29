"""Deck visualization helpers for the dashboard."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ygotrainingbot.format_training import FormatDeck, load_format_pack

YGOPRODECK_CARD_IMAGE = "https://images.ygoprodeck.com/images/cards/{card_id}.jpg"
YGOPRODECK_CARD_IMAGE_SMALL = "https://images.ygoprodeck.com/images/cards_small/{card_id}.jpg"


def card_image_url(card_id: int, *, small: bool = True) -> str:
    template = YGOPRODECK_CARD_IMAGE_SMALL if small else YGOPRODECK_CARD_IMAGE
    return template.format(card_id=card_id)


def load_card_name_index(cache_path: Path | None) -> dict[int, str]:
    if cache_path is None or not cache_path.is_file():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        cards = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(cards, list):
            return {}
        index: dict[int, str] = {}
        for raw in cards:
            if not isinstance(raw, dict):
                continue
            try:
                card_id = int(raw["id"])
            except (KeyError, TypeError, ValueError):
                continue
            name = str(raw.get("name") or f"Card {card_id}")
            index[card_id] = name
        return index
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _zone_entries(
    card_ids: tuple[int, ...],
    *,
    names: dict[int, str],
    small: bool,
) -> list[dict[str, Any]]:
    counts = Counter(card_ids)
    entries: list[dict[str, Any]] = []
    for card_id, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        entries.append(
            {
                "id": card_id,
                "name": names.get(card_id, f"Card #{card_id}"),
                "count": count,
                "image_url": card_image_url(card_id, small=small),
            }
        )
    return entries


def deck_to_visual(
    deck: FormatDeck | dict[str, Any],
    *,
    names: dict[int, str] | None = None,
    small: bool = True,
) -> dict[str, Any]:
    if isinstance(deck, FormatDeck):
        main = deck.main
        extra = deck.extra
        side = deck.side
        name = deck.name
        archetype = deck.archetype
    else:
        main = tuple(deck.get("main", ()))
        extra = tuple(deck.get("extra", ()) or ())
        side = tuple(deck.get("side", ()) or ())
        name = str(deck.get("name", ""))
        archetype = str(deck.get("archetype", ""))
    name_index = names or {}
    return {
        "name": name,
        "archetype": archetype,
        "main_count": len(main),
        "extra_count": len(extra),
        "side_count": len(side),
        "main": _zone_entries(tuple(main), names=name_index, small=small),
        "extra": _zone_entries(tuple(extra), names=name_index, small=small),
        "side": _zone_entries(tuple(side), names=name_index, small=small),
    }


def find_deck_in_pack(pack_path: Path, deck_name: str) -> FormatDeck:
    pack = load_format_pack(pack_path)
    for deck in pack.decks:
        if deck.name == deck_name:
            return deck
    raise KeyError(deck_name)
