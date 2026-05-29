"""Head-to-head experiment runner with confidence intervals (Phase 6)."""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PlayDuelFn = Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    games: int
    deck_a_wins: int
    deck_b_wins: int
    draws: int
    deck_a_win_rate: float
    ci_low: float
    ci_high: float
    avg_decisions: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "games": self.games,
            "deck_a_wins": self.deck_a_wins,
            "deck_b_wins": self.deck_b_wins,
            "draws": self.draws,
            "deck_a_win_rate": self.deck_a_win_rate,
            "confidence_interval_95": [self.ci_low, self.ci_high],
            "avg_decisions": self.avg_decisions,
        }


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    if trials <= 0:
        return 0.0, 0.0
    phat = successes / trials
    denominator = 1 + z**2 / trials
    centre = phat + z**2 / (2 * trials)
    margin = z * math.sqrt((phat * (1 - phat) + z**2 / (4 * trials)) / trials)
    low = (centre - margin) / denominator
    high = (centre + margin) / denominator
    return max(0.0, low), min(1.0, high)


def run_head_to_head_experiment(
    *,
    play_duel: PlayDuelFn,
    gateway_command: str,
    deck_a: object,
    deck_b: object,
    agent_a: str,
    agent_b: str,
    policy_a: str,
    policy_b: str,
    weights_a: Path | None,
    weights_b: Path | None,
    games: int,
    timeout_seconds: float,
    format_name: str,
    rng: random.Random | None = None,
    alternate_first: bool = True,
) -> ExperimentResult:
    from ygotrainingbot.league_tournament import generate_duel_seed

    randomizer = rng or random.Random()
    wins = Counter()
    total_decisions = 0

    for game_number in range(1, games + 1):
        first_is_a = True if not alternate_first else game_number % 2 == 1
        first_agent = agent_a if first_is_a else agent_b
        second_agent = agent_b if first_is_a else agent_a
        first_policy = policy_a if first_is_a else policy_b
        second_policy = policy_b if first_is_a else policy_a
        first_weights = weights_a if first_is_a else weights_b
        second_weights = weights_b if first_is_a else weights_a
        first_deck = deck_a if first_is_a else deck_b
        second_deck = deck_b if first_is_a else deck_a
        seed = generate_duel_seed(randomizer)
        report = play_duel(
            gateway_command,
            first_agent=first_agent,
            second_agent=second_agent,
            first_policy=first_policy,
            second_policy=second_policy,
            first_weights=first_weights,
            second_weights=second_weights,
            first_deck=first_deck,
            second_deck=second_deck,
            seed=seed,
            timeout_seconds=timeout_seconds,
            format_name=format_name,
            game_number=game_number,
            goes_first=first_agent,
        )
        total_decisions += int(report.get("traced_decisions", 0))
        winner = next(iter(dict(report.get("wins_by_agent", {}))), None)
        if winner == agent_a:
            wins["a"] += 1
        elif winner == agent_b:
            wins["b"] += 1
        else:
            wins["draw"] += 1

    decisive = wins["a"] + wins["b"]
    rate = wins["a"] / decisive if decisive else 0.0
    low, high = wilson_interval(wins["a"], decisive)
    return ExperimentResult(
        games=games,
        deck_a_wins=wins["a"],
        deck_b_wins=wins["b"],
        draws=wins["draw"],
        deck_a_win_rate=rate,
        ci_low=low,
        ci_high=high,
        avg_decisions=total_decisions / max(1, games),
    )


def ratio_experiment_summary(results: dict[int, ExperimentResult]) -> dict[str, Any]:
    return {
        str(copies): result.to_dict()
        for copies, result in sorted(results.items())
    }
