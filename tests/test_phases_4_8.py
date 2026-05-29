import json
from pathlib import Path

from ygotrainingbot.duel_analytics import analytics_to_learning_nudges, deck_analytics
from ygotrainingbot.experiments import wilson_interval
from ygotrainingbot.loop_guard import free_gigabytes
from ygotrainingbot.research_assistant import answer_training_question
from ygotrainingbot.training_db import bot_game_record, connect, index_game_log


def _write_game(path: Path, *, home: str, away: str, winner: str, goes_first: str, traces: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "meta": {
                    "year": 2010,
                    "home_bot_id": home,
                    "away_bot_id": away,
                    "home_name": home,
                    "away_name": away,
                    "goes_first": goes_first,
                },
                "result": {
                    "winner": winner,
                    "loser": away if winner == home else home,
                    "end_reason": "lp",
                    "decisions": len(traces),
                    "script_stats": {"runtime_errors": 0},
                },
                "traces": traces,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_training_db_indexes_and_queries(tmp_path: Path) -> None:
    root = tmp_path / "bracket"
    game_a = root / "2010" / "games" / "bot-01_vs_bot-02" / "series-000" / "game-01.json"
    game_b = root / "2010" / "games" / "bot-01_vs_bot-03" / "series-000" / "game-01.json"
    trace = {
        "turn": 1,
        "agent": "bot-01",
        "selected_action": "attack-0",
        "selected_label": "Attack",
        "selected_tags": ["attack"],
    }
    _write_game(game_a, home="bot-01", away="bot-02", winner="bot-01", goes_first="bot-01", traces=[trace])
    _write_game(game_b, home="bot-01", away="bot-03", winner="bot-03", goes_first="bot-03", traces=[trace])

    db = tmp_path / "training.db"
    conn = connect(db)
    index_game_log(conn, game_a)
    index_game_log(conn, game_b)
    conn.commit()

    record = bot_game_record(conn, "bot-01")
    assert record["games"] == 2
    assert record["wins"] == 1
    assert record["losses"] == 1
    assert bot_game_record(conn, "bot-01", goes_first=True)["wins"] == 1


def test_research_assistant_answers_bot_record(tmp_path: Path) -> None:
    db = tmp_path / "training.db"
    game = tmp_path / "game.json"
    _write_game(
        game,
        home="bot-01",
        away="bot-02",
        winner="bot-01",
        goes_first="bot-01",
        traces=[{"turn": 1, "agent": "bot-01", "selected_action": "a", "selected_label": "A", "selected_tags": []}],
    )
    conn = connect(db)
    index_game_log(conn, game)
    conn.commit()
    answer = answer_training_question(db, "How is Yugi doing overall?", default_bot_id="bot-01")
    assert "bot-01" in answer
    assert "1" in answer


def test_analytics_to_learning_nudges_penalizes_passive_play() -> None:
    nudges = analytics_to_learning_nudges(
        {
            "top_tags": [("phase", 40), ("decline", 30)],
            "passive_tag_rate": 0.4,
            "going_first": {"win_rate": 0.6, "games": 5},
            "going_second": {"win_rate": 0.2, "games": 5},
        }
    )
    assert nudges["phase"] < 0
    assert nudges["attack"] > 0


def test_wilson_interval_bounds() -> None:
    low, high = wilson_interval(7, 10)
    assert 0.0 <= low <= high <= 1.0


def test_free_gigabytes_positive(tmp_path: Path) -> None:
    assert free_gigabytes(tmp_path) > 0


def test_chronological_plan_defaults() -> None:
    from ygotrainingbot.chronological import build_chronological_plan, chronological_pack_paths

    root = Path(__file__).resolve().parents[1]
    packs = chronological_pack_paths(root)
    assert len(packs) == 2
    plan = build_chronological_plan(repo_root=root, bracket_years=[2010])
    assert plan["bracket_years"] == [2010]
    assert len(plan["formats"]) == 2


def test_ensure_disk_headroom_raises_when_insufficient(tmp_path: Path, monkeypatch) -> None:
    from ygotrainingbot.loop_guard import ensure_disk_headroom

    monkeypatch.setattr(
        "ygotrainingbot.loop_guard.free_gigabytes",
        lambda _path: 0.01,
    )
    try:
        ensure_disk_headroom(tmp_path, min_free_gb=1.0)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "Insufficient disk space" in str(exc)


def test_run_head_to_head_experiment_aggregates_wins() -> None:
    from ygotrainingbot.experiments import run_head_to_head_experiment

    def fake_play(_gateway: str, **kwargs) -> dict[str, object]:
        winner = kwargs["first_agent"] if kwargs["game_number"] % 2 == 1 else kwargs["second_agent"]
        return {"wins_by_agent": {winner: 1}, "traced_decisions": 10}

    result = run_head_to_head_experiment(
        play_duel=fake_play,
        gateway_command="node gateway.mjs",
        deck_a=(1, 2, 3),
        deck_b=(4, 5, 6),
        agent_a="deck-a",
        agent_b="deck-b",
        policy_a="control",
        policy_b="control",
        weights_a=None,
        weights_b=None,
        games=4,
        timeout_seconds=30.0,
        format_name="test",
    )
    assert result.games == 4
    assert result.deck_a_wins + result.deck_b_wins == 4
    assert 0.0 <= result.deck_a_win_rate <= 1.0
