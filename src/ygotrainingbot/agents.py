"""Agent interfaces and simple baseline policies."""

from __future__ import annotations

import random
from dataclasses import dataclass
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


class RandomLegalActionAgent:
    """Seeded random legal-action baseline for benchmark comparisons."""

    def __init__(self, name: str = "random-legal", seed: int = 1337) -> None:
        self._name = name
        self._random = random.Random(seed)

    @property
    def name(self) -> str:
        return self._name

    def choose_action(self, state: VisibleGameState) -> GameAction:
        if not state.legal_actions:
            raise ValueError(f"State {state.state_id!r} has no legal actions.")
        return self._random.choice(state.legal_actions)


@dataclass(frozen=True, slots=True)
class ActionEvaluation:
    """A scored legal action considered by a heuristic agent."""

    action_id: str
    label: str
    score: float
    tags: tuple[str, ...]


class ScoredHeuristicAgent:
    """Base class for deterministic, inspectable heuristic policies."""

    TAG_SCORES = {
        "lethal": 10_000,
        "destroy-monster": 160,
        "direct-attack": 80,
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
    LABEL_SCORES = {
        "go to end phase": -80,
        "go to battle phase": 25,
        "go to main phase 2": -10,
        "do not chain": -30,
        "do not activate": -40,
        "yes": 5,
        "no": -5,
    }

    def __init__(self, name: str = "heuristic") -> None:
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

    def evaluate_actions(self, state: VisibleGameState) -> tuple[ActionEvaluation, ...]:
        """Return every legal action with the policy's score."""

        return tuple(
            ActionEvaluation(
                action_id=action.action_id,
                label=action.label,
                score=self._score(action),
                tags=action.tags,
            )
            for action in state.legal_actions
        )

    def explain_decision(self, state: VisibleGameState, action: GameAction) -> str:
        """Return a compact explanation suitable for trace metadata."""

        ranked = sorted(
            self.evaluate_actions(state),
            key=lambda evaluation: evaluation.score,
            reverse=True,
        )
        alternatives = [
            {
                "action_id": evaluation.action_id,
                "label": evaluation.label,
                "score": evaluation.score,
                "tags": list(evaluation.tags),
            }
            for evaluation in ranked[:5]
        ]
        return (
            f"selected_score={self._score(action):.2f}; "
            f"top_alternatives={alternatives}"
        )

    def _score(self, action: GameAction) -> float:
        score = action.expected_value or 0.0
        for tag in action.tags:
            score += self.TAG_SCORES.get(tag, 0)
            if tag.startswith("damage:"):
                score += _numeric_tag_value(tag) * self.damage_weight
            elif tag.startswith("lp-swing:"):
                score += _numeric_tag_value(tag) * self.lp_swing_weight
            elif tag.startswith("opp-lp:"):
                score += max(0.0, 8000.0 - _numeric_tag_value(tag)) * self.pressure_weight
        label = action.label.lower()
        for phrase, phrase_score in self.LABEL_SCORES.items():
            if phrase in label:
                score += phrase_score
        return score

    @property
    def damage_weight(self) -> float:
        return 0.08

    @property
    def lp_swing_weight(self) -> float:
        return 0.06

    @property
    def pressure_weight(self) -> float:
        return 0.01


class HeuristicActionAgent(ScoredHeuristicAgent):
    """Compatibility alias for the first proactive policy."""

    def __init__(self, name: str = "heuristic-aggressive") -> None:
        super().__init__(name)


class AggressiveHeuristicAgent(ScoredHeuristicAgent):
    """Policy that strongly prioritizes damage, attacks, and lethal lines."""

    TAG_SCORES = {
        **ScoredHeuristicAgent.TAG_SCORES,
        "attack": 180,
        "direct-attack": 160,
        "normal-summon": 120,
        "special-summon": 130,
        "phase": -60,
        "set-spell": 5,
    }

    @property
    def damage_weight(self) -> float:
        return 0.18


class TempoHeuristicAgent(ScoredHeuristicAgent):
    """Policy that values board development and battle phase access."""

    TAG_SCORES = {
        **ScoredHeuristicAgent.TAG_SCORES,
        "normal-summon": 140,
        "special-summon": 150,
        "activate": 90,
        "set-monster": 65,
        "zone": 30,
        "phase": -35,
    }


class ControlHeuristicAgent(ScoredHeuristicAgent):
    """Policy that prefers interaction, setting cards, and preserving options."""

    TAG_SCORES = {
        **ScoredHeuristicAgent.TAG_SCORES,
        "activate": 100,
        "effect": 95,
        "chain": 70,
        "set-spell": 80,
        "set-monster": 60,
        "attack": 55,
        "phase": -10,
    }


def create_agent(policy: str, name: str | None = None) -> DuelAgent:
    """Create an agent by policy slug."""

    normalized = policy.strip().lower()
    if normalized in {"first", "first-legal", "first-legal-action", "baseline"}:
        return FirstLegalActionAgent(name or "first-legal-action")
    if normalized in {"random", "random-legal"}:
        return RandomLegalActionAgent(name or "random-legal")
    if normalized in {"heuristic", "heuristic-aggressive"}:
        return HeuristicActionAgent(name or "heuristic-aggressive")
    if normalized in {"aggressive", "aggressive-heuristic"}:
        return AggressiveHeuristicAgent(name or "aggressive-heuristic")
    if normalized in {"tempo", "tempo-heuristic"}:
        return TempoHeuristicAgent(name or "tempo-heuristic")
    if normalized in {"control", "control-heuristic"}:
        return ControlHeuristicAgent(name or "control-heuristic")
    raise ValueError(f"Unknown agent policy {policy!r}.")


def _numeric_tag_value(tag: str) -> float:
    try:
        return float(tag.split(":", 1)[1])
    except (IndexError, ValueError):
        return 0.0
