"""Agent interfaces and simple baseline policies."""

from __future__ import annotations

from typing import Protocol

from ygotrainingbot.models import GameAction, VisibleGameState


class DuelAgent(Protocol):
    """An opponent or coach policy that can choose legal duel actions."""

    @property
    def name(self) -> str:
        """Human-readable agent name used in traces."""

    def choose_action(self, state: VisibleGameState) -> GameAction:
        """Choose one action from the state's legal action list."""


class FirstLegalActionAgent:
    """Deterministic baseline that makes tests and fixtures reproducible."""

    def __init__(self, name: str = "first-legal-action") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def choose_action(self, state: VisibleGameState) -> GameAction:
        if not state.legal_actions:
            raise ValueError(f"State {state.state_id!r} has no legal actions.")
        return state.legal_actions[0]


class HeuristicActionAgent:
    """Simple gameplay policy that prefers proactive legal actions.

    This is intentionally deterministic so win-rate comparisons are reproducible.
    It is not trying to be strong yet; it is the first measurable step above
    "pick the first legal action."
    """

    _TAG_SCORES = {
        "attack": 100,
        "normal-summon": 90,
        "special-summon": 88,
        "activate": 70,
        "effect": 65,
        "set-monster": 45,
        "set-spell": 35,
        "select-card": 30,
        "zone": 20,
        "position": 15,
        "chain": 5,
        "phase": -20,
    }
    _LABEL_SCORES = {
        "go to end phase": -80,
        "go to battle phase": 25,
        "go to main phase 2": -10,
        "do not chain": -30,
        "do not activate": -40,
        "yes": 5,
        "no": -5,
    }

    def __init__(self, name: str = "heuristic-aggressive") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def choose_action(self, state: VisibleGameState) -> GameAction:
        if not state.legal_actions:
            raise ValueError(f"State {state.state_id!r} has no legal actions.")
        return max(
            enumerate(state.legal_actions),
            key=lambda indexed_action: (self._score(indexed_action[1]), -indexed_action[0]),
        )[1]

    def _score(self, action: GameAction) -> float:
        score = action.expected_value or 0.0
        for tag in action.tags:
            score += self._TAG_SCORES.get(tag, 0)
        label = action.label.lower()
        for phrase, phrase_score in self._LABEL_SCORES.items():
            if phrase in label:
                score += phrase_score
        return score


def create_agent(policy: str, name: str | None = None) -> DuelAgent:
    """Create an agent by policy slug."""

    normalized = policy.strip().lower()
    if normalized in {"first", "first-legal", "first-legal-action", "baseline"}:
        return FirstLegalActionAgent(name or "first-legal-action")
    if normalized in {"heuristic", "heuristic-aggressive", "aggressive"}:
        return HeuristicActionAgent(name or "heuristic-aggressive")
    raise ValueError(f"Unknown agent policy {policy!r}.")
