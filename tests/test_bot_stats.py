"""Tests for per-bot training stats aggregation."""

from __future__ import annotations

import json
from pathlib import Path

from ygotrainingbot.bot_stats import BotStatsPaths, load_bot_stats, rebuild_bot_stats, write_bot_stats
from ygotrainingbot.policy_runtime import write_policy_file
def test_rebuild_bot_stats_counts_job_and_policy(tmp_path: Path) -> None:
    roster = tmp_path / "roster.json"
    roster.write_text(
        json.dumps(
            {
                "bots": [
                    {"bot_id": "bot-01", "name": "Yugi", "policy": "heuristic"},
                ]
            }
        ),
        encoding="utf-8",
    )
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "job-1"
    job_dir.mkdir(parents=True)
    job_dir.joinpath("meta.json").write_text(
        json.dumps(
            {
                "job_id": "job-1",
                "status": "completed",
                "bot_id": "bot-01",
                "finished_at": 1_700_000_000.0,
            }
        ),
        encoding="utf-8",
    )
    job_dir.joinpath("report.json").write_text(
        json.dumps(
            {
                "total_games": 4,
                "total_traced_decisions": 40,
                "game_log_paths": [],
            }
        ),
        encoding="utf-8",
    )
    bots_dir = tmp_path / "bots"
    policy_path = bots_dir / "bot-01" / "policy.json"
    write_policy_file(policy_path, {"attack": 1.0}, observations=40, parent_observations=0)

    catalog = tmp_path / "human-duels"
    duels_dir = catalog / "duels"
    duels_dir.mkdir(parents=True)
    duel_path = duels_dir / "duel-1.json"
    duel_path.write_text(
        json.dumps(
            {
                "meta": {
                    "format": "test",
                    "study_agent": "Yugi",
                    "player_a": "Yugi",
                    "player_b": "AI",
                },
                "result": {"winner": "Yugi", "turns": 8},
                "decisions": [
                    {
                        "turn": 1,
                        "agent": "Yugi",
                        "selected_action": "a1",
                        "selected_label": "Summon",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (catalog / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "duel_id": "duel-1",
                    "path": str(duel_path),
                    "format": "test",
                    "study_agent": "Yugi",
                    "player_a": "Yugi",
                    "player_b": "AI",
                    "winner": "Yugi",
                    "decision_count": 1,
                    "imported_at": "2026-01-01T00:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    paths = BotStatsPaths(
        repo_root=tmp_path,
        jobs_dir=jobs_dir,
        bots_dir=bots_dir,
        human_catalog_dir=catalog,
        roster_path=roster,
        stats_path=tmp_path / "bot-training-stats.json",
    )
    snapshot = rebuild_bot_stats(paths)
    bot = snapshot["bots"][0]
    assert bot["training_duels"] == 4
    assert bot["human_duels"] == 1
    assert bot["training_sessions"] == 1
    assert bot["policy_updates"] >= 1
    assert bot["total_decisions"] >= 41

    write_bot_stats(paths)
    assert paths.stats_path.is_file()


def test_load_bot_stats_tolerates_empty_or_invalid_cache(tmp_path: Path) -> None:
    stats_path = tmp_path / "bot-training-stats.json"
    stats_path.write_text("", encoding="utf-8")
    assert load_bot_stats(stats_path) == {"updated_at": None, "bots": [], "totals": {}}

    stats_path.write_text("not json", encoding="utf-8")
    assert load_bot_stats(stats_path)["bots"] == []
