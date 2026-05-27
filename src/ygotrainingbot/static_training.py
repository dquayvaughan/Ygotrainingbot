"""Immediate static training over card text and set composition."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable

from ygotrainingbot.models import Card, CardSet

EFFECT_PATTERNS = {
    "banish": re.compile(r"\bbanish(?:es|ed)?\b", re.IGNORECASE),
    "chain": re.compile(r"\bchain\b", re.IGNORECASE),
    "destroy": re.compile(r"\bdestroy(?:s|ed)?\b", re.IGNORECASE),
    "draw": re.compile(r"\bdraw\b", re.IGNORECASE),
    "graveyard": re.compile(r"\bgraveyard|gy\b", re.IGNORECASE),
    "negate": re.compile(r"\bnegate(?:s|d)?\b", re.IGNORECASE),
    "once-per-turn": re.compile(r"\bonce per turn\b", re.IGNORECASE),
    "quick-effect": re.compile(r"\bquick effect\b", re.IGNORECASE),
    "search": re.compile(r"\badd 1 .* from your deck|search\b", re.IGNORECASE),
    "special-summon": re.compile(r"\bspecial summon\b", re.IGNORECASE),
}


@dataclass(frozen=True, slots=True)
class InteractionCandidate:
    """A possible interaction the bot should verify in real simulations."""

    set_code: str
    set_name: str
    cards: tuple[str, ...]
    shared_signals: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class SetProfile:
    """Static summary of a set's teachable themes."""

    set_code: str
    set_name: str
    card_count: int
    top_archetypes: tuple[tuple[str, int], ...]
    top_effect_tags: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class StaticTrainingReport:
    """Output from a static training pass over card sets."""

    sets_analyzed: int
    cards_analyzed: int
    top_effect_tags: tuple[tuple[str, int], ...]
    set_profiles: tuple[SetProfile, ...]
    interaction_candidates: tuple[InteractionCandidate, ...]


class StaticSetTrainer:
    """Mine current card data for set themes and likely interaction hotspots."""

    def train(
        self,
        card_sets: Iterable[CardSet],
        *,
        max_sets: int | None = None,
        max_candidates_per_set: int = 10,
    ) -> StaticTrainingReport:
        selected_sets = tuple(card_sets)[:max_sets]
        profiles: list[SetProfile] = []
        candidates: list[InteractionCandidate] = []
        global_tags: Counter[str] = Counter()
        cards_analyzed = 0

        for card_set in selected_sets:
            cards_analyzed += len(card_set.cards)
            profile = self._profile_set(card_set)
            profiles.append(profile)
            global_tags.update(dict(profile.top_effect_tags))
            candidates.extend(
                self._find_candidates(card_set, max_candidates=max_candidates_per_set)
            )

        return StaticTrainingReport(
            sets_analyzed=len(selected_sets),
            cards_analyzed=cards_analyzed,
            top_effect_tags=tuple(global_tags.most_common(12)),
            set_profiles=tuple(profiles),
            interaction_candidates=tuple(candidates),
        )

    def _profile_set(self, card_set: CardSet) -> SetProfile:
        archetypes: Counter[str] = Counter()
        effect_tags: Counter[str] = Counter()

        for card in card_set.cards:
            archetypes.update(card.archetypes)
            effect_tags.update(effect_tags_for(card))

        return SetProfile(
            set_code=card_set.code,
            set_name=card_set.name,
            card_count=len(card_set.cards),
            top_archetypes=tuple(archetypes.most_common(8)),
            top_effect_tags=tuple(effect_tags.most_common(8)),
        )

    def _find_candidates(
        self,
        card_set: CardSet,
        *,
        max_candidates: int,
    ) -> tuple[InteractionCandidate, ...]:
        cards_by_signal: dict[str, list[Card]] = defaultdict(list)

        for card in card_set.cards:
            signals = set(card.archetypes) | set(effect_tags_for(card))
            for signal in signals:
                cards_by_signal[signal].append(card)

        candidates: list[InteractionCandidate] = []
        seen_pairs: set[tuple[str, str, str]] = set()
        for signal, cards in sorted(cards_by_signal.items()):
            if len(cards) < 2:
                continue
            for first, second in combinations(cards[:8], 2):
                key = tuple(sorted((first.card_id, second.card_id))) + (signal,)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                candidates.append(
                    InteractionCandidate(
                        set_code=card_set.code,
                        set_name=card_set.name,
                        cards=(first.name, second.name),
                        shared_signals=(signal,),
                        reason=f"Both cards share the {signal!r} signal.",
                    )
                )
                if len(candidates) >= max_candidates:
                    return tuple(candidates)

        return tuple(candidates)


def effect_tags_for(card: Card) -> tuple[str, ...]:
    """Return normalized effect tags inferred from card text."""

    return tuple(
        tag
        for tag, pattern in EFFECT_PATTERNS.items()
        if pattern.search(card.text)
    )
