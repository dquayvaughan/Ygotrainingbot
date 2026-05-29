"""Main/extra deck list helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

# ocgcore card type bits (see ocgcore-wasm OcgType)
TYPE_MONSTER = 0x1
TYPE_FUSION = 0x40
TYPE_RITUAL = 0x80
TYPE_SYNCHRO = 0x2000
TYPE_XYZ = 0x800000
TYPE_LINK = 0x4000000
EXTRA_MONSTER_TYPES = TYPE_FUSION | TYPE_RITUAL | TYPE_SYNCHRO | TYPE_XYZ | TYPE_LINK


@dataclass(frozen=True, slots=True)
class DeckZones:
    main: tuple[int, ...]
    extra: tuple[int, ...] = ()

    def validate(self) -> None:
        if len(self.main) < 40:
            raise ValueError(f"main deck must contain at least 40 cards (got {len(self.main)}).")
        if len(self.extra) > 15:
            raise ValueError(f"extra deck may contain at most 15 cards (got {len(self.extra)}).")

    @property
    def all_cards(self) -> tuple[int, ...]:
        return self.main + self.extra


def is_extra_deck_monster(card_type: int) -> bool:
    return bool((card_type & TYPE_MONSTER) and (card_type & EXTRA_MONSTER_TYPES))


def split_main_and_extra(
    card_ids: Sequence[int],
    *,
    card_type: Callable[[int], int],
) -> DeckZones:
    """Partition card IDs into main and extra using ocgcore type metadata."""

    main: list[int] = []
    extra: list[int] = []
    for card_id in card_ids:
        if is_extra_deck_monster(card_type(int(card_id))):
            extra.append(int(card_id))
        else:
            main.append(int(card_id))
    return DeckZones(main=tuple(main), extra=tuple(extra))


def deck_zones_from_format_deck(deck: object) -> DeckZones:
    """Build deck zones from a FormatDeck-like object."""

    main = tuple(getattr(deck, "main"))
    extra = tuple(getattr(deck, "extra", ()) or ())
    zones = DeckZones(main=main, extra=extra)
    zones.validate()
    return zones
