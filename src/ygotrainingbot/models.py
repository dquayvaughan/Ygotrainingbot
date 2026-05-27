"""Domain models shared by training, simulation, and coaching code."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, Sequence


class CardType(StrEnum):
    """High-level Yu-Gi-Oh! card categories."""

    MONSTER = "monster"
    SPELL = "spell"
    TRAP = "trap"


@dataclass(frozen=True, slots=True)
class Card:
    """Structured card metadata used by experiments."""

    card_id: str
    name: str
    card_type: CardType
    text: str = ""
    archetypes: tuple[str, ...] = ()
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CardSet:
    """A reproducible pool of cards to explore."""

    code: str
    name: str
    cards: tuple[Card, ...]
    release_year: int | None = None

    def card_names(self) -> tuple[str, ...]:
        """Return card names in the stable order provided by the set source."""

        return tuple(card.name for card in self.cards)


@dataclass(frozen=True, slots=True)
class Deck:
    """A concrete deck list for one side of an experiment."""

    name: str
    main: tuple[Card, ...]
    extra: tuple[Card, ...] = ()
    side: tuple[Card, ...] = ()

    def contains(self, card_name: str) -> bool:
        """Return whether the deck contains a card by exact name."""

        return any(card.name == card_name for card in (*self.main, *self.extra, *self.side))


@dataclass(frozen=True, slots=True)
class GameAction:
    """A legal action exposed to an agent for a single decision point."""

    action_id: str
    label: str
    expected_value: float | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VisibleGameState:
    """The information available to an agent when making a decision."""

    state_id: str
    turn: int
    active_player: str
    summary: str
    legal_actions: tuple[GameAction, ...]
    public_zones: Mapping[str, Sequence[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DuelTrace:
    """A single decision made during a duel."""

    state: VisibleGameState
    action: GameAction
    agent_name: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Outcome and trace data for a simulated duel."""

    winner: str | None
    loser: str | None
    turns: int
    traces: tuple[DuelTrace, ...]
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def is_draw(self) -> bool:
        """Return whether the match ended without a winner."""

        return self.winner is None


@dataclass(frozen=True, slots=True)
class CoachingRecommendation:
    """Actionable feedback derived from one or more duel traces."""

    title: str
    scenario: str
    recommendation: str
    evidence: tuple[str, ...]
    confidence: float
