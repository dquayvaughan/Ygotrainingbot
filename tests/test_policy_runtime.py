import json
from pathlib import Path

from ygotrainingbot.agents import create_agent
from ygotrainingbot.league_tournament import filter_report_for_bot
from ygotrainingbot.models import GameAction, VisibleGameState
from ygotrainingbot.policy_runtime import (
    DEFAULT_LEARNED_WEIGHT_SCALE,
    reset_cycle_observations,
    resolve_bot_agent,
    scaled_tag_weights_for_play,
    should_accept_policy_update,
    write_policy_file,
)


def test_scaled_tag_weights_for_play(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    write_policy_file(policy, {"attack": 2.0}, observations=1)
    scaled = scaled_tag_weights_for_play(policy)
    assert scaled is not None
    assert scaled["attack"] == 2.0 * DEFAULT_LEARNED_WEIGHT_SCALE


def test_learned_weights_change_agent_choice(tmp_path: Path) -> None:
    state = VisibleGameState(
        state_id="s1",
        turn=1,
        active_player="bot-a",
        summary="chain",
        legal_actions=(
            GameAction("decline", "Do not chain", None, ("decline",)),
            GameAction("activate-0", "Activate trap", None, ("negate", "trap")),
        ),
        public_zones={},
    )
    passive = create_agent("heuristic", "bot-a", {"negate": -500.0})
    reactive = create_agent("heuristic", "bot-a", {"negate": 500.0})
    assert passive.choose_action(state).action_id == "decline"
    assert reactive.choose_action(state).action_id == "activate-0"


def test_filter_report_for_bot_keeps_only_bot_samples() -> None:
    report = {
        "format": "season-2010",
        "wins_by_agent": {"bot-01": 1, "bot-02": 0},
        "traces": [],
        "decision_samples": [],
        "game_log_path": None,
        "traced_decisions": 2,
        "tags": {"attack": 2, "phase": 1},
        "action_counts": {"attack-0": 1, "to-end-phase": 1},
    }
    report["decision_samples"] = [
        {
            "turn": 1,
            "agent": "bot-01",
            "selected_action": "attack-0",
            "selected_tags": ["attack"],
            "selected_label": "Attack",
            "evaluation": "",
        },
        {
            "turn": 1,
            "agent": "bot-02",
            "selected_action": "to-end-phase",
            "selected_tags": ["phase"],
            "selected_label": "End",
            "evaluation": "",
        },
    ]
    filtered = filter_report_for_bot(report, "bot-01")
    assert filtered["traced_decisions"] == 1
    assert filtered["tags"] == {"attack": 1}
    assert filtered["action_counts"] == {"attack-0": 1}


def test_should_accept_policy_update_with_wins() -> None:
    report = {"bot_agent": "bot-01", "games": 10, "wins_by_agent": {"bot-01": 6}}
    assert should_accept_policy_update(report, {"attack": 0.0}, {"attack": 3.0})


def test_should_accept_policy_update_infers_bot_agent_from_format() -> None:
    report = {
        "format": "season-2010:bot-01",
        "games": 80,
        "wins_by_agent": {"bot-01": 39},
    }
    assert resolve_bot_agent(report) == "bot-01"
    assert should_accept_policy_update(report, {"attack": 0.0}, {"attack": 20.0, "removal": 20.0})


def test_should_reject_large_update_without_wins() -> None:
    report = {"format": "season-2010", "games": 80, "wins_by_agent": {}}
    assert not should_accept_policy_update(report, {"attack": 0.0}, {"attack": 20.0, "removal": 20.0})


def test_reset_cycle_observations_uses_parent_baseline(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    write_policy_file(
        policy,
        {"attack": 1.0},
        observations=999,
        parent_observations=100,
    )
    baseline = reset_cycle_observations(policy)
    payload = json.loads(policy.read_text(encoding="utf-8"))
    assert baseline == 100
    assert payload["observations"] == 100
    assert payload["tag_weights"]["attack"] == 1.0
