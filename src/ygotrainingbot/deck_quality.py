"""Validate tournament deck lists before training."""

from __future__ import annotations

from typing import Any, Sequence


def deck_card_set(deck: dict[str, Any]) -> set[int]:
    cards: set[int] = set()
    for zone in ("main", "extra", "side"):
        for card_id in deck.get(zone, []) or []:
            cards.add(int(card_id))
    return cards


def count_signature_hits(deck: dict[str, Any], signature_ids: Sequence[int]) -> int:
    if not signature_ids:
        return 0
    cards = deck_card_set(deck)
    return sum(1 for card_id in signature_ids if int(card_id) in cards)


def is_synthesized_shell(deck: dict[str, Any]) -> bool:
    source = str(deck.get("source", ""))
    name = str(deck.get("name", ""))
    return "Representative" in source and "topping shell" in source or "representative top shell" in name.lower()


def validate_tournament_deck(
    deck: dict[str, Any],
    *,
    archetype: str,
    signature_ids: Sequence[int],
    search_keywords: Sequence[str] = (),
) -> list[str]:
    """Return human-readable issues; empty list means the deck looks usable."""

    issues: list[str] = []
    main = deck.get("main", []) or []
    if len(main) < 40:
        issues.append(f"main deck has {len(main)} cards (need >= 40)")

    if is_synthesized_shell(deck):
        issues.append("synthesized placeholder shell (no real tournament list)")

    name = str(deck.get("name", "")).lower()
    keywords = [token for token in search_keywords if len(token) >= 4]

    if signature_ids and count_signature_hits(deck, signature_ids) == 0:
        name_matches = any(keyword in name for keyword in keywords)
        has_tournament_url = bool(deck.get("ygoprodeck_url"))
        if not (name_matches or has_tournament_url):
            issues.append(f"no key cards for {archetype}")

    if keywords and count_signature_hits(deck, signature_ids) == 0:
        if not any(keyword in name for keyword in keywords) and not deck.get("ygoprodeck_url"):
            issues.append("deck name does not match archetype keywords")

    return issues


def cache_url_is_overused(
    deck: dict[str, Any],
    *,
    archetype: str,
    cache: dict[str, dict[str, Any]],
    search_keywords: Sequence[str] = (),
) -> bool:
    url = str(deck.get("ygoprodeck_url", "")).strip()
    if not url:
        return False
    matches = [key for key, entry in cache.items() if str(entry.get("ygoprodeck_url", "")).strip() == url]
    if len(matches) <= 1:
        return False
    name = str(deck.get("name", "")).lower()
    keywords = [token for token in search_keywords if len(token) >= 4]
    return not any(keyword in name for keyword in keywords) and archetype not in name
