import json
from pathlib import Path

from ygotrainingbot.duel_logs import samples_from_game_log_payload
from ygotrainingbot.human_duels import (
    build_learning_report,
    catalog_summary,
    import_human_duel_files,
    import_human_duels,
    normalize_human_duel_payload,
    validate_human_duel,
)
from ygotrainingbot.learning import learn_from_report


def test_validate_decisions_only_payload() -> None:
    payload = {
        "meta": {"format": "goat-2005"},
        "decisions": [{"turn": 1, "agent": "p1", "selected_action": "attack-0"}],
    }
    assert validate_human_duel(payload) == []


def test_validate_rejects_empty_payload() -> None:
    assert validate_human_duel({})


def test_normalize_decisions_to_traces() -> None:
    payload = {
        "meta": {"format": "test"},
        "decisions": [
            {
                "turn": 2,
                "agent": "alice",
                "selected_action": "attack-0",
                "selected_tags": ["attack"],
                "evaluation": "selected_score=10.00; top_alternatives=[]",
            }
        ],
    }
    normalized = normalize_human_duel_payload(payload, source_path=Path("x.json"))
    assert normalized["meta"]["source"] == "human"
    assert len(normalized["traces"]) == 1
    samples = samples_from_game_log_payload(normalized)
    assert samples[0]["selected_action"] == "attack-0"
    assert samples[0]["agent"] == "alice"


def test_import_and_learn_from_human_duels(tmp_path: Path) -> None:
    input_dir = tmp_path / "incoming"
    input_dir.mkdir()
    duel = {
        "meta": {
            "format": "unit-human",
            "study_agent": "pro",
            "player_a": "pro",
            "player_b": "scrub",
        },
        "result": {"winner": "pro", "loser": "scrub", "turns": 5},
        "decisions": [
            {
                "turn": 1,
                "agent": "pro",
                "selected_action": "attack-0",
                "selected_label": "Direct attack",
                "selected_tags": ["attack", "direct-attack"],
                "evaluation": "selected_score=500.00; top_alternatives=[]",
            },
            {
                "turn": 1,
                "agent": "scrub",
                "selected_action": "decline",
                "selected_tags": ["decline"],
            },
        ],
    }
    (input_dir / "duel-one.json").write_text(json.dumps(duel), encoding="utf-8")

    catalog_dir = tmp_path / "catalog"
    result = import_human_duels(input_dir, catalog_dir=catalog_dir)
    assert result.ok
    assert len(result.imported) == 1
    assert (catalog_dir / "manifest.json").is_file()
    assert (catalog_dir / "duels" / "duel-one.json").is_file()

    report = build_learning_report(catalog_dir, study_agent="pro", format_filter="unit-human")
    assert report["total_games"] == 1
    assert report["total_traced_decisions"] == 1
    assert report["bot_agent"] == "pro"
    assert "attack" in report["tags"]

    report_path = catalog_dir / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    policy_path = catalog_dir / "policy.json"
    _analysis, english = learn_from_report(report_path, policy_path)
    assert "human:unit-human" in english
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    assert policy["tag_weights"].get("attack", 0) > 0


def test_import_errors_on_invalid_json(tmp_path: Path) -> None:
    input_dir = tmp_path / "bad"
    input_dir.mkdir()
    (input_dir / "broken.json").write_text("{}", encoding="utf-8")
    result = import_human_duels(input_dir, catalog_dir=tmp_path / "catalog")
    assert not result.ok
    assert result.errors


def test_import_human_duel_files_from_bytes(tmp_path: Path) -> None:
    duel = {
        "meta": {"format": "goat-2005", "study_agent": "alice"},
        "decisions": [{"turn": 1, "agent": "alice", "selected_action": "attack-0", "selected_tags": ["attack"]}],
    }
    catalog_dir = tmp_path / "catalog"
    result = import_human_duel_files(
        [("my-duel.json", json.dumps(duel).encode("utf-8"))],
        catalog_dir=catalog_dir,
    )
    assert result.ok
    assert len(result.imported) == 1
    summary = catalog_summary(catalog_dir)
    assert summary["duel_count"] == 1
    assert summary["formats"] == ["goat-2005"]
