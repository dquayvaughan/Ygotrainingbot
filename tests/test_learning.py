import json
from pathlib import Path

from ygotrainingbot.learning import (
    _outcome_weight_adjustments,
    _samples_for_learning,
    _tempo_weight_adjustments,
    learn_from_report,
)


def test_learn_from_report_writes_policy_and_plain_english(tmp_path: Path) -> None:
    report = {
        "format": "unit-test-format",
        "total_games": 1,
        "total_traced_decisions": 2,
        "matchups": [
            {
                "deck_a": "A",
                "deck_b": "B",
                "report": {
                    "games": 1,
                    "draws": 1,
                    "wins_by_agent": {},
                    "traced_decisions": 2,
                    "tags": {"phase": 1, "decline": 1, "attack": 1, "removal": 1},
                    "action_counts": {"to-end-phase": 1, "attack-0": 1},
                    "decision_samples": [
                        {
                            "turn": 1,
                            "agent": "bot-a",
                            "summary": "Main phase",
                            "selected_action": "to-end-phase",
                            "selected_label": "Go to End Phase",
                            "selected_tags": ["phase"],
                            "selected_expected_value": None,
                            "public_zones": {"life_points": ["bot-a:8000", "bot-b:8000"]},
                            "evaluation": (
                                "selected_score=-90.00; top_alternatives=["
                                "{'action_id': 'normal-summon-0', 'label': 'Normal Summon', "
                                "'score': 90.0, 'tags': ['normal-summon']}]"
                            ),
                        },
                        {
                            "turn": 2,
                            "agent": "bot-b",
                            "summary": "Battle phase",
                            "selected_action": "attack-0",
                            "selected_label": "Direct attack for 1500",
                            "selected_tags": ["attack", "direct-attack", "damage:1500"],
                            "selected_expected_value": 15.0,
                            "public_zones": {"life_points": ["bot-a:6500", "bot-b:8000"]},
                            "evaluation": "selected_score=250.00; top_alternatives=[]",
                        },
                    ],
                },
            }
        ],
    }
    report_path = tmp_path / "report.json"
    policy_path = tmp_path / "policy.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    _analysis, english = learn_from_report(report_path, policy_path)

    policy = json.loads(policy_path.read_text())
    assert "Yu-Gi-Oh bot learning report" in english
    assert "Likely mistakes" in english
    assert "Why the best line was better" in english
    assert "normal-summon-0" in english
    assert "score edge" in english
    assert policy["observations"] == 2
    assert policy["tag_weights"]["phase"] < 0
    assert policy["tag_weights"]["attack"] > 0
    assert policy["tag_weights"]["normal-summon"] > 0


def test_learn_from_report_surfaces_gateway_failures(tmp_path: Path) -> None:
    report = {
        "format": "banlist-2023-11",
        "total_games": 25,
        "matchups": [
            {
                "deck_a": "Ryzeal",
                "deck_b": "Memento",
                "report": {
                    "games": 25,
                    "draws": 0,
                    "traced_decisions": 0,
                    "wins_by_agent": {},
                    "failed_games": [
                        {
                            "game": game_index,
                            "error": (
                                "EDOPro gateway exited before producing a result. stderr: "
                                "Error: Deck script validation failed: missingScripts=90590304"
                            ),
                        }
                        for game_index in range(1, 26)
                    ],
                },
            }
        ],
    }
    report_path = tmp_path / "failed-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    _analysis, english = learn_from_report(report_path, tmp_path / "policy.json")

    assert _analysis["failed_games"] == 25
    assert _analysis["completed_games"] == 0
    assert "Games failed: 25" in english
    assert "Gateway failures" in english
    assert "missingScripts=90590304" in english
    assert "No weight changes yet." in english


def test_learn_from_report_applies_mistake_mining(tmp_path: Path) -> None:
    report_path = tmp_path / "mistake-report.json"
    policy_path = tmp_path / "policy.json"
    report_path.write_text(
        json.dumps(
            {
                "format": "mistake-test",
                "games": 1,
                "draws": 0,
                "traced_decisions": 1,
                "tags": {"phase": 1},
                "decision_samples": [
                    {
                        "turn": 1,
                        "agent": "bot-01",
                        "summary": "Main phase",
                        "selected_action": "to-end-phase",
                        "selected_tags": ["phase"],
                        "evaluation": (
                            "selected_score=-90.00; top_alternatives=["
                            "{'action_id': 'attack-0', 'label': 'Attack', "
                            "'score': 250.0, 'tags': ['attack', 'direct-attack']}]"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    learn_from_report(report_path, policy_path)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    assert policy["tag_weights"]["attack"] > 0
    assert policy["tag_weights"]["phase"] < 0


def test_tempo_weight_adjustments_only_when_duels_run_long() -> None:
    assert _tempo_weight_adjustments({}) == {}
    assert _tempo_weight_adjustments({"avg_duel_turns": 8.0}) == {}
    adjustments = _tempo_weight_adjustments({"avg_duel_turns": 16.0})
    assert adjustments["attack"] > 0
    assert adjustments["lethal"] > adjustments["attack"]
    assert adjustments["phase"] < 0


def test_outcome_weight_adjustments_favor_fast_win_tags() -> None:
    samples = (
        [{"game_won": True, "game_turns": 6, "selected_tags": ["attack"]} for _ in range(4)]
        + [{"game_won": True, "game_turns": 16, "selected_tags": ["phase"]} for _ in range(4)]
        + [{"game_won": False, "game_turns": 16, "selected_tags": ["attack"]} for _ in range(4)]
    )
    adjustments = _outcome_weight_adjustments(samples)
    assert adjustments.get("attack", 0) > adjustments.get("phase", 0)


def test_learn_from_report_applies_tempo_nudges_from_game_logs(tmp_path: Path) -> None:
    game_path = tmp_path / "game-01.json"
    game_path.write_text(
        json.dumps(
            {
                "meta": {"goes_first": "bot-a"},
                "result": {"winner": "bot-a", "turns": 18},
                "traces": [
                    {
                        "agent": "bot-a",
                        "turn": 3,
                        "summary": "Battle Phase",
                        "selected_action": "attack-0",
                        "selected_label": "Attack",
                        "selected_tags": ["attack", "direct-attack"],
                        "legal_actions": [],
                        "note": "selected_score=100.00; top_alternatives=[]",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "long-duel-report.json"
    report_path.write_text(
        json.dumps(
            {
                "format": "tempo-test",
                "total_games": 1,
                "bot_agent": "bot-a",
                "game_log_paths": [str(game_path)],
            }
        ),
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.json"

    _analysis, english = learn_from_report(report_path, policy_path)

    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    assert policy["tag_weights"]["attack"] > 0
    assert policy["tag_weights"]["phase"] < 0
    assert "tempo weight nudges" in english


def test_samples_for_learning_skip_stale_turn_limit() -> None:
    samples = [
        {
            "end_reason": "turn_limit",
            "life_points": [8000, 8000],
            "selected_tags": ["set-spell"],
        },
        {
            "end_reason": "turn_limit",
            "life_points": [7500, 8000],
            "selected_tags": ["attack"],
        },
        {
            "end_reason": "lp",
            "life_points": [0, 8000],
            "selected_tags": ["lethal"],
        },
    ]
    kept = _samples_for_learning(samples)
    assert len(kept) == 2
    assert kept[0]["selected_tags"] == ["attack"]
    assert kept[1]["selected_tags"] == ["lethal"]


def test_learned_policy_contains_version_metadata(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    policy_path = tmp_path / "policy.json"
    report_path.write_text(
        json.dumps({"format": "empty", "games": 0, "draws": 0, "traced_decisions": 0}),
        encoding="utf-8",
    )

    learn_from_report(report_path, policy_path)

    policy = json.loads(policy_path.read_text())
    assert policy["version"] >= 1
    assert "updated_at" in policy
    assert "parent_observations" in policy
