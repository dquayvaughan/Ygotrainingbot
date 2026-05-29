import json
from pathlib import Path

from ygotrainingbot.duel_logs import game_log_path_for_series, write_game_log
from ygotrainingbot.models import DuelTrace, GameAction, MatchResult, VisibleGameState


def test_write_game_log_persists_full_traces(tmp_path: Path) -> None:
    state = VisibleGameState(
        state_id="s1",
        turn=1,
        active_player="bot-a",
        summary="test",
        legal_actions=(
            GameAction(action_id="attack", label="Attack", expected_value=None, tags=("attack",)),
        ),
        public_zones={},
    )
    trace = DuelTrace(
        state=state,
        action=state.legal_actions[0],
        agent_name="bot-a",
        note="ok",
    )
    result = MatchResult(
        winner="bot-a",
        loser="bot-b",
        turns=3,
        traces=(trace,),
        tags=("edopro",),
        metadata={"end_reason": "win", "life_points": [0, 8000], "gateway_logs": ("log",)},
    )
    path = tmp_path / "game.json"
    write_game_log(path, meta={"match": "test"}, result=result)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["result"]["end_reason"] == "win"
    assert len(payload["traces"]) == 1
    assert len(payload["traces"][0]["legal_actions"]) == 1
    assert payload["engine_logs"] == ["log"]


def test_load_decision_samples_from_game_log(tmp_path: Path) -> None:
    from ygotrainingbot.duel_logs import load_decision_samples_for_learning, write_game_log

    state = VisibleGameState(
        state_id="s1",
        turn=1,
        active_player="bot-a",
        summary="test",
        legal_actions=(
            GameAction(action_id="attack", label="Attack", expected_value=None, tags=("attack",)),
        ),
        public_zones={},
    )
    trace = DuelTrace(
        state=state,
        action=state.legal_actions[0],
        agent_name="bot-a",
        note="selected_score=10.00; top_alternatives=[]",
    )
    result = MatchResult(
        winner="bot-a",
        loser="bot-b",
        turns=1,
        traces=(trace,),
        tags=(),
        metadata={"end_reason": "win"},
    )
    path = tmp_path / "game.json"
    write_game_log(path, meta={}, result=result)
    samples = load_decision_samples_for_learning({"game_log_path": str(path)})
    assert len(samples) == 1
    assert samples[0]["selected_action"] == "attack"
    assert samples[0]["evaluation"] == "selected_score=10.00; top_alternatives=[]"


def test_game_log_path_for_series_layout(tmp_path: Path) -> None:
    path = game_log_path_for_series(
        tmp_path,
        year=2010,
        home_bot_id="bot-01",
        away_bot_id="bot-02",
        series_index=4,
        game_number=2,
    )
    assert path == tmp_path / "2010" / "games" / "bot-01_vs_bot-02" / "series-004" / "game-02.json"
