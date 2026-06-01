"""Tests for duel live logging."""

from __future__ import annotations

from pathlib import Path

from ygotrainingbot.duel_live_log import DuelLiveLog, set_duel_live_context


def test_duel_live_log_writes_timestamped_lines(tmp_path: Path) -> None:
    path = tmp_path / "duel-live.log"
    logger = DuelLiveLog(path)
    set_duel_live_context(matchup="K9 vs Ryzeal", game=1, games_total=3)
    logger.duel_start(
        game=1,
        games_total=3,
        first_agent="bot-a",
        second_agent="bot-b",
        policy_a="search-control",
        policy_b="search-control",
        seed=(1, 2, 3, 4),
    )
    logger.duel_decision(
        decision_index=1,
        agent="bot-a",
        action_id="activate-0",
        label="Activate Pot of Prosperity",
        summary="EDOPro select_idlecmd | LP bot-a:8000 bot-b:8000",
    )
    logger.duel_end(
        {
            "game_number": 1,
            "end_reason": "lp",
            "traced_decisions": 42,
            "wins_by_agent": {"bot-a": 1},
            "life_points": [6200, 0],
        }
    )
    text = path.read_text(encoding="utf-8")
    assert ">> Duel 1/3" in text
    assert "G1 #1 bot-a -> activate-0" in text
    assert "OK G1 done: winner=bot-a" in text
    assert "42 decisions" in text
