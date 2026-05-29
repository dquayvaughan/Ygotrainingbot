import json
from pathlib import Path

from ygotrainingbot.cli import main


def test_train_bot_league_trains_independent_opponents_and_reports(tmp_path: Path, monkeypatch, capsys) -> None:
    pack_a = tmp_path / "goat-2005.json"
    pack_b = tmp_path / "edison-2010.json"
    pack_a.write_text(
        json.dumps(
            {
                "name": "pack-a",
                "decks": [
                    {"name": "alpha", "archetype": "test-a", "main": [1184620] * 40},
                    {"name": "beta", "archetype": "test-b", "main": [3134241] * 40},
                ],
            }
        ),
        encoding="utf-8",
    )
    pack_b.write_text(
        json.dumps(
            {
                "name": "pack-b",
                "decks": [
                    {"name": "gamma", "archetype": "test-c", "main": [40044918] * 40},
                    {"name": "delta", "archetype": "test-d", "main": [46986414] * 40},
                ],
            }
        ),
        encoding="utf-8",
    )
    edopro_home = tmp_path / "edopro-home"
    edopro_home.mkdir()
    main_weights = tmp_path / "main-policy.json"
    main_weights.write_text(json.dumps({"tag_weights": {"attack": 1.0}, "observations": 1}) + "\n", encoding="utf-8")

    trained_policies: list[str] = []

    def fake_train_format_curriculum(*args, **kwargs):
        promote_to = Path(kwargs["promote_to"])
        promote_to.parent.mkdir(parents=True, exist_ok=True)
        promote_to.write_text(
            json.dumps({"tag_weights": {"attack": 2.0}, "observations": 10}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        trained_policies.append(str(promote_to))
        return 0

    def fake_compare_agents_report(*args, **kwargs):
        baseline_weights = kwargs["baseline_weights"]
        return {
            "candidate_win_rate": 0.75,
            "candidate_decisive_win_rate": 0.8,
            "candidate_wins": 12,
            "baseline_wins": 4,
            "draws": 0,
            "total_games": 16,
            "baseline_weights": str(baseline_weights) if baseline_weights else None,
        }

    monkeypatch.setattr("ygotrainingbot.cli._train_format_curriculum", fake_train_format_curriculum)
    monkeypatch.setattr("ygotrainingbot.cli._compare_agents_report", fake_compare_agents_report)

    output_dir = tmp_path / "league-out"
    exit_code = main(
        [
            "train-bot-league",
            "--packs",
            str(pack_a),
            str(pack_b),
            "--edopro-home",
            str(edopro_home),
            "--main-policy",
            "aggressive",
            "--main-weights",
            str(main_weights),
            "--opponents",
            "3",
            "--games-per-matchup",
            "2",
            "--evaluation-games-per-matchup",
            "2",
            "--max-decisions",
            "40",
            "--output-dir",
            str(output_dir),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert len(trained_policies) == 3
    assert len(payload["ranked_opponents"]) == 3
    assert payload["main_weights"] == str(main_weights)
    assert payload["main_collective_policy"]
    assert all("bot-" in row["bot"] for row in payload["ranked_opponents"])
    assert all(row["name"] for row in payload["ranked_opponents"])
    assert all(row["assigned_decks"] for row in payload["ranked_opponents"])
    assert all(row["characteristics"] for row in payload["ranked_opponents"])
    assert (output_dir / "league-report.json").exists()


def test_train_bot_league_uses_persisted_roster(tmp_path: Path, monkeypatch, capsys) -> None:
    pack = tmp_path / "goat-2005.json"
    pack.write_text(
        json.dumps(
            {
                "name": "pack-a",
                "decks": [{"name": "alpha", "archetype": "test-a", "main": [1184620] * 40}],
            }
        ),
        encoding="utf-8",
    )
    edopro_home = tmp_path / "edopro-home"
    edopro_home.mkdir()
    output_dir = tmp_path / "league-out"
    roster_path = output_dir / "roster.json"
    roster_path.parent.mkdir(parents=True, exist_ok=True)
    roster_path.write_text(
        json.dumps(
            {
                "bots": [
                    {
                        "assigned_deck": {"pack": str(pack), "name": "alpha", "archetype": "test-a"},
                        "assigned_decks": {str(pack): {"pack": str(pack), "name": "alpha", "archetype": "test-a"}},
                        "bot_id": "bot-01",
                        "characteristics": "custom-style",
                        "initial_weights": {"attack": 9.0},
                        "name": "Zane",
                        "policy": "control",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    main_weights = tmp_path / "main-policy.json"
    main_weights.write_text(json.dumps({"tag_weights": {"attack": 1.0}, "observations": 1}) + "\n", encoding="utf-8")

    captured_policy: list[str] = []

    def fake_train_format_curriculum(*args, **kwargs):
        captured_policy.append(str(kwargs["policy"]))
        promote_to = Path(kwargs["promote_to"])
        promote_to.parent.mkdir(parents=True, exist_ok=True)
        promote_to.write_text(json.dumps({"tag_weights": {"attack": 2.0}, "observations": 1}) + "\n", encoding="utf-8")
        return 0

    def fake_compare_agents_report(*args, **kwargs):
        return {
            "candidate_win_rate": 0.5,
            "candidate_decisive_win_rate": 0.5,
            "candidate_wins": 1,
            "baseline_wins": 1,
            "draws": 0,
            "total_games": 2,
        }

    monkeypatch.setattr("ygotrainingbot.cli._train_format_curriculum", fake_train_format_curriculum)
    monkeypatch.setattr("ygotrainingbot.cli._compare_agents_report", fake_compare_agents_report)

    exit_code = main(
        [
            "train-bot-league",
            "--packs",
            str(pack),
            "--edopro-home",
            str(edopro_home),
            "--main-weights",
            str(main_weights),
            "--opponents",
            "1",
            "--output-dir",
            str(output_dir),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured_policy == ["control"]
    assert payload["ranked_opponents"][0]["name"] == "Zane"
    assert payload["ranked_opponents"][0]["characteristics"] == "custom-style"
