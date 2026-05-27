"""Training orchestration for set-by-set exploration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ygotrainingbot.agents import DuelAgent
from ygotrainingbot.models import CardSet, Deck, MatchResult
from ygotrainingbot.simulation import DuelSimulator


@dataclass(frozen=True, slots=True)
class SetExplorationPlan:
    """A reproducible batch of duels for one card pool."""

    card_set: CardSet
    decks: tuple[Deck, ...]
    repetitions: int = 1

    def validate(self) -> None:
        if self.repetitions < 1:
            raise ValueError("repetitions must be at least 1.")
        if len(self.decks) < 2:
            raise ValueError("at least two decks are required for exploration.")


@dataclass(frozen=True, slots=True)
class LearningReport:
    """Aggregate output from a training or exploration run."""

    card_set_code: str
    matches_played: int
    traced_decisions: int
    wins_by_agent: dict[str, int]
    recurring_tags: dict[str, int]

    @property
    def total_decisions(self) -> int:
        """Return the number of traced decisions represented by the report."""

        return self.traced_decisions


class SelfPlayRunner:
    """Run repeated simulator games and summarize what happened."""

    def __init__(self, simulator: DuelSimulator) -> None:
        self._simulator = simulator

    def run(
        self,
        plan: SetExplorationPlan,
        first_agent: DuelAgent,
        second_agent: DuelAgent,
    ) -> tuple[LearningReport, tuple[MatchResult, ...]]:
        plan.validate()
        results: list[MatchResult] = []

        for _ in range(plan.repetitions):
            results.append(self._simulator.play(first_agent, second_agent))

        return self._summarize(plan, results), tuple(results)

    def _summarize(
        self,
        plan: SetExplorationPlan,
        results: Iterable[MatchResult],
    ) -> LearningReport:
        wins_by_agent: dict[str, int] = {}
        recurring_tags: dict[str, int] = {}
        matches_played = 0
        traced_decisions = 0

        for result in results:
            matches_played += 1
            traced_decisions += len(result.traces)
            if result.winner is not None:
                wins_by_agent[result.winner] = wins_by_agent.get(result.winner, 0) + 1
            for trace in result.traces:
                for tag in trace.action.tags:
                    recurring_tags[tag] = recurring_tags.get(tag, 0) + 1
            for tag in result.tags:
                recurring_tags[tag] = recurring_tags.get(tag, 0) + 1

        return LearningReport(
            card_set_code=plan.card_set.code,
            matches_played=matches_played,
            traced_decisions=traced_decisions,
            wins_by_agent=wins_by_agent,
            recurring_tags=recurring_tags,
        )
