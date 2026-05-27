import json
import sys
from pathlib import Path

from ygotrainingbot import EdoproGatewayConfig, EdoproInstall, FirstLegalActionAgent
from ygotrainingbot.cli import main
from ygotrainingbot.edopro import JsonLineEdoproSimulator


FAKE_GATEWAY = """
import json
import sys

start = json.loads(sys.stdin.readline())
assert start["type"] == "start_duel"
assert start["players"] == ["bot-a", "bot-b"]

print(json.dumps({
    "type": "state",
    "state": {
        "state_id": "edopro-turn-1",
        "turn": 1,
        "active_player": "bot-a",
        "summary": "EDOPro legal action window",
        "legal_actions": [
            {"action_id": "normal-summon", "label": "Normal Summon", "tags": ["edopro"]},
            {"action_id": "set-pass", "label": "Set and pass"}
        ],
        "public_zones": {"bot-a.field": ["Starter Synchron"]}
    }
}), flush=True)

action = json.loads(sys.stdin.readline())
assert action["type"] == "action"
assert action["state_id"] == "edopro-turn-1"
assert action["agent"] == "bot-a"
assert action["action_id"] == "normal-summon"

print(json.dumps({
    "type": "result",
    "winner": "bot-a",
    "loser": "bot-b",
    "turns": 1,
    "tags": ["edopro", "fake-gateway"]
}), flush=True)
"""


def test_edopro_gateway_simulator_round_trips_actions(tmp_path: Path) -> None:
    gateway = _write_fake_gateway(tmp_path)
    config = EdoproGatewayConfig.from_shell_words((sys.executable, str(gateway)))

    result = JsonLineEdoproSimulator(config).play(
        FirstLegalActionAgent("bot-a"),
        FirstLegalActionAgent("bot-b"),
    )

    assert result.winner == "bot-a"
    assert result.loser == "bot-b"
    assert result.turns == 1
    assert result.tags == ("edopro", "fake-gateway")
    assert len(result.traces) == 1
    assert result.traces[0].action.action_id == "normal-summon"
    assert result.traces[0].state.public_zones == {"bot-a.field": ("Starter Synchron",)}


def test_edopro_install_validation_accepts_standard_layout(tmp_path: Path) -> None:
    (tmp_path / "script").mkdir()
    (tmp_path / "deck").mkdir()
    (tmp_path / "cards.cdb").write_text("", encoding="utf-8")

    install = EdoproInstall(root=tmp_path).with_defaults()

    assert install.validation_errors() == ()


def test_cli_edopro_play_once_uses_gateway(tmp_path: Path, capsys) -> None:
    gateway = _write_fake_gateway(tmp_path)

    exit_code = main(
        [
            "edopro-play-once",
            "--gateway-command",
            f"{sys.executable} {gateway}",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["winner"] == "bot-a"
    assert payload["traced_decisions"] == 1


def _write_fake_gateway(tmp_path: Path) -> Path:
    gateway = tmp_path / "fake_edopro_gateway.py"
    gateway.write_text(FAKE_GATEWAY, encoding="utf-8")
    return gateway
