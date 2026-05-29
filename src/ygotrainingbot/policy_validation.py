"""Sim-based validation for learned policy updates."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Callable, Sequence

from ygotrainingbot.league_tournament import BotSeasonState, generate_duel_seed

PlayDuelFn = Callable[..., dict[str, Any]]


def _protagonist_won(report: dict[str, Any], protagonist_id: str) -> bool | None:
    wins = dict(report.get("wins_by_agent", {}))
    if not wins:
        return None
    winner = next(iter(wins), None)
    if not winner:
        return None
    return str(winner) == protagonist_id


def validate_protagonist_policy_update(
    *,
    protagonist: BotSeasonState,
    opponents: Sequence[BotSeasonState],
    backup_weights: Path,
    candidate_weights: Path,
    play_duel: PlayDuelFn,
    gateway_command: str,
    games_per_matchup: int,
    timeout_seconds: float,
    format_name: str,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Paired head-to-head validation: candidate vs backup protagonist weights."""

    randomizer = rng or random.Random()
    candidate_wins = 0
    baseline_wins = 0
    draws = 0
    matchups: list[dict[str, Any]] = []

    for opponent in opponents:
        matchup_candidate = 0
        matchup_baseline = 0
        for game_number in range(1, games_per_matchup + 1):
            first_is_protagonist = game_number % 2 == 1
            seed = generate_duel_seed(randomizer)

            if first_is_protagonist:
                first_bot, second_bot = protagonist, opponent
                candidate_first, candidate_second = candidate_weights, opponent.policy_path
                baseline_first, baseline_second = backup_weights, opponent.policy_path
            else:
                first_bot, second_bot = opponent, protagonist
                candidate_first, candidate_second = opponent.policy_path, candidate_weights
                baseline_first, baseline_second = opponent.policy_path, backup_weights

            candidate_report = play_duel(
                gateway_command,
                first_agent=first_bot.bot_id,
                second_agent=second_bot.bot_id,
                first_policy=first_bot.policy,
                second_policy=second_bot.policy,
                first_weights=candidate_first,
                second_weights=candidate_second,
                first_deck=first_bot.deck,
                second_deck=second_bot.deck,
                seed=seed,
                timeout_seconds=timeout_seconds,
                format_name=format_name,
                game_number=game_number,
                goes_first=first_bot.bot_id,
            )
            candidate_result = _protagonist_won(candidate_report, protagonist.bot_id)

            baseline_report = play_duel(
                gateway_command,
                first_agent=first_bot.bot_id,
                second_agent=second_bot.bot_id,
                first_policy=first_bot.policy,
                second_policy=second_bot.policy,
                first_weights=baseline_first,
                second_weights=baseline_second,
                first_deck=first_bot.deck,
                second_deck=second_bot.deck,
                seed=seed,
                timeout_seconds=timeout_seconds,
                format_name=format_name,
                game_number=game_number,
                goes_first=first_bot.bot_id,
            )
            baseline_result = _protagonist_won(baseline_report, protagonist.bot_id)

            if candidate_result is True:
                candidate_wins += 1
                matchup_candidate += 1
            if baseline_result is True:
                baseline_wins += 1
                matchup_baseline += 1
            if candidate_result is None and baseline_result is None:
                draws += 1

        matchups.append(
            {
                "opponent": opponent.bot_id,
                "opponent_name": opponent.name,
                "candidate_wins": matchup_candidate,
                "baseline_wins": matchup_baseline,
            }
        )

    decisive = candidate_wins + baseline_wins
    return {
        "protagonist": protagonist.bot_id,
        "games_per_matchup": games_per_matchup,
        "opponents_tested": len(opponents),
        "candidate_wins": candidate_wins,
        "baseline_wins": baseline_wins,
        "draws": draws,
        "candidate_win_rate": candidate_wins / decisive if decisive else 0.0,
        "accepted": candidate_wins >= baseline_wins,
        "matchups": matchups,
    }
