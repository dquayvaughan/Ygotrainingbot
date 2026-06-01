import json
from pathlib import Path

from ygotrainingbot.duel_logs import (
    find_retry_stuck_end_event,
    format_gateway_health_lines,
    gateway_health_from_report,
    write_game_log,
)
from ygotrainingbot.learning import learn_from_report
from ygotrainingbot.models import DuelTrace, GameAction, MatchResult, VisibleGameState


def test_find_retry_stuck_end_event_reads_dict_logs() -> None:
    event = find_retry_stuck_end_event(
        [
            {"event": "submit_response"},
            {
                "event": "retry_stuck_end",
                "last_prompt_name": "select_idlecmd",
                "tried_action_ids": ["set-spell-0", "to-end-phase"],
                "message_types": ["retry"],
            },
        ]
    )
    assert event is not None
    assert event["last_prompt_name"] == "select_idlecmd"


def test_gateway_health_aggregates_stuck_games(tmp_path: Path) -> None:
    game_log = tmp_path / "game-01.json"
    game_log.write_text(
        json.dumps(
            {
                "meta": {},
                "result": {"end_reason": "retry_stuck", "turns": 5, "decisions": 20},
                "traces": [],
                "engine_logs": [
                    {
                        "event": "retry_stuck_end",
                        "last_prompt_name": "select_idlecmd",
                        "tried_action_ids": ["activate-0", "set-spell-1"],
                        "message_types": ["retry"],
                        "decisions": 20,
                        "duel_turn": 3,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report = {
        "format": "banlist-2026-01",
        "games": 1,
        "game_log_paths": [str(game_log)],
    }

    health = gateway_health_from_report(report, report_path=tmp_path / "report.json")
    assert health["stuck_games"] == 1
    assert health["top_prompts"] == [("select_idlecmd", 1)]
    assert ("activate-0", 1) in health["top_tried_actions"]

    lines = format_gateway_health_lines(health)
    assert any("Gateway health" in line for line in lines)
    assert any("select_idlecmd" in line for line in lines)


def test_learn_from_report_includes_gateway_health_section(tmp_path: Path) -> None:
    state = VisibleGameState(
        state_id="s1",
        turn=1,
        active_player="bot-a",
        summary="Main phase",
        legal_actions=(
            GameAction(action_id="set-spell-0", label="Set card", tags=("set-spell",)),
        ),
        public_zones={},
    )
    trace = DuelTrace(
        state=state,
        action=state.legal_actions[0],
        agent_name="bot-a",
        note="selected_score=10; top_alternatives=[]",
    )
    result = MatchResult(
        winner=None,
        loser=None,
        turns=5,
        traces=(trace,),
        tags=("edopro", "retry_stuck"),
        metadata={
            "end_reason": "retry_stuck",
            "gateway_logs": [
                {
                    "event": "retry_stuck_end",
                    "last_prompt_name": "select_battlecmd",
                    "tried_action_ids": ["attack-0", "to-end-phase"],
                    "message_types": ["retry"],
                    "decisions": 12,
                    "duel_turn": 4,
                }
            ],
        },
    )
    game_log = tmp_path / "game-01.json"
    write_game_log(game_log, meta={}, result=result)

    report = {
        "format": "banlist-2026-01",
        "total_games": 1,
        "matchups": [
            {
                "report": {
                    "games": 1,
                    "draws": 0,
                    "sim_faults": 1,
                    "traced_decisions": 1,
                    "game_log_paths": [str(game_log)],
                    "decision_samples": [
                        {
                            "duel_turn": 1,
                            "decision_index": 1,
                            "agent": "bot-a",
                            "selected_action": "set-spell-0",
                            "selected_tags": ["set-spell"],
                            "end_reason": "retry_stuck",
                            "game_log_path": str(game_log),
                            "evaluation": "selected_score=10; top_alternatives=[]",
                        }
                    ],
                }
            }
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    _analysis, english = learn_from_report(report_path, tmp_path / "policy.json")

    assert "Gateway health" in english
    assert "select_battlecmd" in english
    assert _analysis["gateway_health"]["stuck_games"] == 1
