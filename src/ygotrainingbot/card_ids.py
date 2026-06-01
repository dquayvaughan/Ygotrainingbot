"""Map YGOPRODeck / artwork card IDs to EDOPro script IDs."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALIAS_PATH = _REPO_ROOT / "configs" / "edopro-card-id-aliases.json"


@lru_cache(maxsize=1)
def edopro_card_id_aliases() -> dict[int, int]:
    if not _ALIAS_PATH.exists():
        return {}
    payload = json.loads(_ALIAS_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    aliases: dict[int, int] = {}
    for raw_source, raw_target in payload.items():
        try:
            source = int(raw_source)
            target = int(raw_target)
        except (TypeError, ValueError):
            continue
        if source != target:
            aliases[source] = target
    return aliases


def canonicalize_card_id(card_id: int) -> int:
    """Return the EDOPro script ID for a deck-list card ID."""

    return edopro_card_id_aliases().get(int(card_id), int(card_id))


def canonicalize_card_ids(cards: Sequence[int]) -> tuple[int, ...]:
    return tuple(canonicalize_card_id(card_id) for card_id in cards)


def canonicalize_card_list(cards: Sequence[int]) -> list[int]:
    return [canonicalize_card_id(card_id) for card_id in cards]
