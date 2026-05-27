"""Simulator boundaries and deterministic fixtures for early experiments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ygotrainingbot.agents import DuelAgent
from ygotrainingbot.models import DuelTrace, MatchResult, VisibleGameState


class DuelSimulator(Protocol):
    """Boundary for a rules-complete Yu-Gi-Oh! simulator backend."""

    def play(self, first_player: DuelAgent, second_player: DuelAgent) -> MatchResult:
        """Play one duel and return the result plus decision traces."""


@dataclass(frozen=True, slots=True)
class DeterministicScenarioSimulator:
    """Replay a fixed sequence of visible states through two agents.

    This fixture keeps the pipeline testable before a complete duel engine is
    available. Each state's ``active_player`` decides which agent acts.
    """

    states: tuple[VisibleGameState, ...]
    winner: str | None
    tags: tuple[str, ...] = ()

    def play(self, first_player: DuelAgent, second_player: DuelAgent) -> MatchResult:
        agents = {
            first_player.name: first_player,
            second_player.name: second_player,
        }
        traces: list[DuelTrace] = []

        for state in self.states:
            try:
                agent = agents[state.active_player]
            except KeyError as exc:
                known_agents = ", ".join(sorted(agents))
                raise ValueError(
                    f"State {state.state_id!r} references unknown active player "
                    f"{state.active_player!r}; known agents: {known_agents}."
                ) from exc

            action = agent.choose_action(state)
            if action not in state.legal_actions:
                raise ValueError(
                    f"Agent {agent.name!r} chose illegal action {action.action_id!r} "
                    f"for state {state.state_id!r}."
                )
            traces.append(DuelTrace(state=state, action=action, agent_name=agent.name))

        loser = None
        if self.winner == first_player.name:
            loser = second_player.name
        elif self.winner == second_player.name:
            loser = first_player.name
        elif self.winner is not None:
            raise ValueError(f"Winner {self.winner!r} is not one of the supplied agents.")

        return MatchResult(
            winner=self.winner,
            loser=loser,
            turns=max((state.turn for state in self.states), default=0),
            traces=tuple(traces),
            tags=self.tags,
        )
