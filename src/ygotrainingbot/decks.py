"""Deck helpers for reliable ocgcore training duels."""

from __future__ import annotations

from typing import Sequence

# Scriptless normal pools used for stable training duels.
TRAINING_NORMAL_FALLBACKS_BALANCED: tuple[int, ...] = (
    1184620,  # Hunter Spider
    3134241,  # Flying Kamakiri #1
    19159413,  # Swordstalker
    75390004,  # 7 Colored Fish
)

TRAINING_NORMAL_FALLBACKS_ELITE: tuple[int, ...] = (
    19159413,  # Swordstalker
    19159413,  # Swordstalker
    75390004,  # 7 Colored Fish
    75390004,  # 7 Colored Fish
)

TRAINING_DECK_PROFILES: dict[str, tuple[int, ...]] = {
    "balanced": TRAINING_NORMAL_FALLBACKS_BALANCED,
    "elite": TRAINING_NORMAL_FALLBACKS_ELITE,
}


def sanitize_training_deck(main: Sequence[int], *, profile: str = "balanced") -> tuple[int, ...]:
    """Replace a main deck with scriptless normals so duels can reach LP/deck-out endings."""

    if len(main) < 40:
        raise ValueError("main deck must contain at least 40 cards.")
    fallbacks = TRAINING_DECK_PROFILES.get(profile)
    if fallbacks is None:
        raise ValueError(f"unknown training deck profile: {profile!r}")
    return tuple(fallbacks[index % len(fallbacks)] for index in range(len(main)))
