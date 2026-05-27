"""Card database ingestion and normalization."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from ygotrainingbot.models import Card, CardSet, CardType

YGOPRODECK_CARDINFO_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"


def fetch_ygoprodeck_cards(timeout_seconds: float = 30.0) -> list[dict[str, Any]]:
    """Fetch the current public Yu-Gi-Oh! card database."""

    request = urllib.request.Request(
        YGOPRODECK_CARDINFO_URL,
        headers={"User-Agent": "ygotrainingbot/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))

    cards = payload.get("data")
    if not isinstance(cards, list):
        raise ValueError("YGOPRODeck response did not contain a data list.")
    return cards


def load_card_database(path: Path) -> list[dict[str, Any]]:
    """Load cached card data from disk."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    raise ValueError(f"Unsupported card cache format in {path}.")


def save_card_database(path: Path, cards: Iterable[dict[str, Any]]) -> None:
    """Save card data in a compact, reproducible cache format."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"data": list(cards)}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def card_from_ygoprodeck(raw_card: dict[str, Any]) -> Card:
    """Convert one YGOPRODeck card object into the local card model."""

    raw_type = str(raw_card.get("type", ""))
    card_type = _normalize_card_type(raw_type)
    archetype = raw_card.get("archetype")
    attributes = {
        key: str(value)
        for key, value in raw_card.items()
        if key
        in {
            "attribute",
            "frameType",
            "level",
            "linkval",
            "race",
            "scale",
            "type",
        }
        and value is not None
    }

    return Card(
        card_id=str(raw_card.get("id", "")),
        name=str(raw_card.get("name", "")),
        card_type=card_type,
        text=str(raw_card.get("desc", "")),
        archetypes=(str(archetype),) if archetype else (),
        attributes=attributes,
    )


def build_card_sets(raw_cards: Iterable[dict[str, Any]]) -> tuple[CardSet, ...]:
    """Group card database rows into set-level card pools."""

    set_cards: dict[str, dict[str, Card]] = {}
    set_names: dict[str, str] = {}

    for raw_card in raw_cards:
        card = card_from_ygoprodeck(raw_card)
        for printing in raw_card.get("card_sets", []) or []:
            if not isinstance(printing, dict):
                continue
            set_name = str(printing.get("set_name", "")).strip()
            if not set_name:
                continue
            set_code = _set_code_from_printing(printing, set_name)
            set_names[set_code] = set_name
            set_cards.setdefault(set_code, {})[card.card_id] = card

    return tuple(
        CardSet(
            code=set_code,
            name=set_names[set_code],
            cards=tuple(cards_by_id.values()),
        )
        for set_code, cards_by_id in sorted(set_cards.items(), key=lambda item: item[0])
    )


def _normalize_card_type(raw_type: str) -> CardType:
    lowered = raw_type.lower()
    if "spell" in lowered:
        return CardType.SPELL
    if "trap" in lowered:
        return CardType.TRAP
    return CardType.MONSTER


def _set_code_from_printing(printing: dict[str, Any], set_name: str) -> str:
    raw_code = str(printing.get("set_code", "")).strip()
    if "-" in raw_code:
        return raw_code.split("-", 1)[0].upper()
    if raw_code:
        return raw_code.upper()
    return re.sub(r"[^A-Z0-9]+", "-", set_name.upper()).strip("-")
