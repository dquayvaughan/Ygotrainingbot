"""Deck zone limits, staple pools, and Bo3 side-deck handling."""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from ygotrainingbot.format_training import FormatDeck

MAIN_MIN = 40
MAIN_MAX = 60
EXTRA_MAX = 15
SIDE_MAX = 15

# Main-deck-safe Edison staples (spells / traps / main-deck monsters only).
MAIN_STAPLES_EDISON: tuple[int, ...] = (
    44330098, 26202165, 5318639, 81439173, 19613556, 87910978, 37520316,
    44095762, 53582587, 64697231, 2295440, 9748752, 47297616, 12538374,
    691925, 67169062, 29401950, 58120309, 97077563, 83764718, 3280747,
    66788016, 19613556, 53129443, 71413901, 77585513,
)

# Extra-deck monsters — never pad into main.
EXTRA_STAPLES_SYNCHRO: tuple[int, ...] = (
    50321796, 52687916, 44508094, 7391448, 26593852, 23693634, 60800381,
    73580471, 76774528, 2403771, 44508094, 4896496, 96381979, 44508094,
)

EXTRA_STAPLES_MODERN: tuple[int, ...] = (
    63542003, 50588353, 63436931, 70771599, 50588353, 63436931, 70771599,
    50588353, 63436931, 70771599, 50588353, 63436931, 70771599, 50588353,
    63436931,
)

# Typical side-deck answers (hate / tech).
SIDE_STAPLES_EDISON: tuple[int, ...] = (
    24508291, 29424961, 26593852, 19613556, 5318639, 44095762, 83764718,
    29401950, 58120309, 50321796, 47297616, 64697231, 691925, 2295440,
    44330098,
)

SIDE_STAPLES_MODERN: tuple[int, ...] = (
    24508291, 94145021, 73642296, 14558127, 23434538, 65681983, 27204311,
    10045474, 38595317, 94145021, 73642296, 14558127, 23434538, 65681983,
    27204311,
)

KNOWN_EXTRA_MONSTER_IDS = frozenset(EXTRA_STAPLES_SYNCHRO + EXTRA_STAPLES_MODERN)


def main_staples_for_era(*, modern: bool = False) -> tuple[int, ...]:
    return MAIN_STAPLES_EDISON if not modern else MAIN_STAPLES_EDISON


def extra_staples_for_era(*, modern: bool = False) -> tuple[int, ...]:
    return EXTRA_STAPLES_MODERN if modern else EXTRA_STAPLES_SYNCHRO


def side_staples_for_era(*, modern: bool = False) -> tuple[int, ...]:
    return SIDE_STAPLES_MODERN if modern else SIDE_STAPLES_EDISON


def strip_extra_monsters_from_main(main: Sequence[int]) -> tuple[int, ...]:
    return tuple(card_id for card_id in main if card_id not in KNOWN_EXTRA_MONSTER_IDS)


def pad_zone(
    cards: list[int],
    pool: Sequence[int],
    *,
    target: int,
    maximum: int,
) -> list[int]:
    index = 0
    while len(cards) < target and len(cards) < maximum:
        cards.append(int(pool[index % len(pool)]))
        index += 1
    return cards[:maximum]


def build_side_deck(
    *,
    modern: bool = False,
    signatures: Sequence[int] = (),
) -> tuple[int, ...]:
    side: list[int] = [int(card_id) for card_id in signatures if card_id not in KNOWN_EXTRA_MONSTER_IDS]
    pool = side_staples_for_era(modern=modern)
    pad_zone(side, pool, target=SIDE_MAX, maximum=SIDE_MAX)
    return tuple(side)


def normalize_deck_dict(
    payload: dict[str, object],
    *,
    modern: bool = False,
    require_side: bool = True,
    pad_zones: bool = True,
) -> dict[str, object]:
    """Ensure main/extra/side zones are legal and separated."""

    main = strip_extra_monsters_from_main([int(card_id) for card_id in payload.get("main", [])])
    main_list = list(main)
    if pad_zones:
        pad_zone(main_list, main_staples_for_era(modern=modern), target=MAIN_MIN, maximum=MAIN_MAX)
    else:
        main_list = main_list[:MAIN_MAX]

    extra = [int(card_id) for card_id in payload.get("extra", []) or []]
    if pad_zones:
        if not extra and payload.get("archetype") not in {"Goat Control", "Chaos Warrior"}:
            extra = list(extra_staples_for_era(modern=modern))
        pad_zone(extra, extra_staples_for_era(modern=modern), target=EXTRA_MAX, maximum=EXTRA_MAX)
    else:
        extra = extra[:EXTRA_MAX]

    side_raw = payload.get("side", []) or []
    if side_raw:
        side = [int(card_id) for card_id in side_raw]
        if pad_zones:
            pad_zone(side, side_staples_for_era(modern=modern), target=SIDE_MAX, maximum=SIDE_MAX)
        else:
            side = side[:SIDE_MAX]
    elif require_side:
        side = list(build_side_deck(modern=modern))
    else:
        side = []

    normalized = dict(payload)
    normalized["main"] = main_list
    normalized["extra"] = extra
    normalized["side"] = side
    return normalized


def normalize_format_deck(deck: FormatDeck, *, modern: bool = False) -> FormatDeck:
    payload = normalize_deck_dict(
        {
            "name": deck.name,
            "archetype": deck.archetype,
            "source": deck.source,
            "main": list(deck.main),
            "extra": list(deck.extra),
            "side": list(deck.side),
        },
        modern=modern,
    )
    return FormatDeck(
        name=str(payload["name"]),
        main=tuple(payload["main"]),
        extra=tuple(payload["extra"]),
        side=tuple(payload["side"]),
        source=str(payload.get("source", deck.source)),
        archetype=str(payload.get("archetype", deck.archetype)),
    )


def effective_deck_for_bo3_game(deck: FormatDeck, game_number: int) -> FormatDeck:
    """Apply a simplified 15-in / 15-out side deck for games 2+ of a Bo3."""

    if game_number <= 1 or not deck.side:
        return deck
    side_count = min(len(deck.side), SIDE_MAX)
    main_list = list(deck.main)
    if len(main_list) <= MAIN_MIN:
        return deck
    side_count = min(side_count, len(main_list) - MAIN_MIN)
    if side_count <= 0:
        return deck
    side_in = list(deck.side[:side_count])
    new_main = tuple(main_list[:-side_count] + side_in)
    if len(new_main) < MAIN_MIN:
        return deck
    return replace(deck, main=new_main[:MAIN_MAX])
