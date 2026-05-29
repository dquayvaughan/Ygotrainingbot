import json
from pathlib import Path

from ygotrainingbot.learning import learn_from_report


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
