import json
from pathlib import Path

from ygotrainingbot.duel_logs import write_game_log
from ygotrainingbot.learning import learn_from_report
from ygotrainingbot.models import DuelTrace, GameAction, MatchResult, VisibleGameState


def test_learn_from_report_reads_full_game_logs(tmp_path: Path) -> None:
    state = VisibleGameState(
        state_id="s1",
        turn=1,
        active_player="bot-a",
        summary="Battle phase",
        legal_actions=(
            GameAction(
                action_id="attack-0",
                label="Attack",
                expected_value=15.0,
                tags=("attack", "direct-attack"),
            ),
        ),
        public_zones={},
    )
    trace = DuelTrace(
        state=state,
        action=state.legal_actions[0],
        agent_name="bot-a",
        note="selected_score=250.00; top_alternatives=[]",
    )
    result = MatchResult(
        winner="bot-a",
        loser="bot-b",
        turns=1,
        traces=(trace,),
        tags=("attack",),
        metadata={"end_reason": "win"},
    )
    game_log = tmp_path / "game-01.json"
    write_game_log(game_log, meta={"bot": "bot-a"}, result=result)

    report = {
        "format": "game-log-format",
        "games": 1,
        "draws": 0,
        "traced_decisions": 1,
        "wins_by_agent": {"bot-a": 1},
        "tags": {"attack": 1},
        "action_counts": {"attack-0": 1},
        "game_log_paths": [str(game_log)],
        "decision_samples": [],
    }
    report_path = tmp_path / "report.json"
    policy_path = tmp_path / "policy.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    _analysis, english = learn_from_report(report_path, policy_path)

    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    assert policy["observations"] == 1
    assert policy["tag_weights"]["attack"] > 0
    assert "attack-0" in english or "Attack" in english
