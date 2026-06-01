import json
from pathlib import Path

from ygotrainingbot.edopro_bot_server import EdoproBotServer


def test_edopro_bot_server_decide_and_finish(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"tag_weights": {"attack": 1.0}}), encoding="utf-8")
    catalog = tmp_path / "human-duels"

    server = EdoproBotServer(
        policy_path=policy_path,
        catalog_dir=catalog,
        bot_policy="heuristic",
        learn_after_duel=False,
    )

    status, start = server.handle(
        "POST",
        "/v1/start",
        {"human_player": "you", "bot_player": "bot", "format": "test"},
    )
    assert status == 200
    session_id = start["session_id"]

    status, decision = server.handle(
        "POST",
        "/v1/decide",
        {
            "session_id": session_id,
            "summary": "Battle phase",
            "duel_turn": 2,
            "legal_actions": [
                {"action_id": "attack-0", "label": "Attack", "tags": ["attack"]},
                {"action_id": "to-end-phase", "label": "End Phase", "tags": ["phase"]},
            ],
        },
    )
    assert status == 200
    assert decision["action_id"] == "attack-0"

    status, finish = server.handle(
        "POST",
        "/v1/finish",
        {"session_id": session_id, "winner": "you", "loser": "bot", "turns": 4},
    )
    assert status == 200
    assert Path(finish["duel_log_path"]).is_file()
    assert finish["imported"] == 1
