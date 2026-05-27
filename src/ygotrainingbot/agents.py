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
