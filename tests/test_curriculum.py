import json
from pathlib import Path

from ygotrainingbot.cli import main


def test_train_format_curriculum_carries_policy_between_stages(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    pack_a = tmp_path / "goat-2005.json"
    pack_b = tmp_path / "edison-2010.json"
    pack_a.write_text("{}", encoding="utf-8")
    pack_b.write_text("{}", encoding="utf-8")
    edopro_home = tmp_path / "edopro-home"
    edopro_home.mkdir()

    compare_calls: list[dict[str, object]] = []

    def fake_train_format_pack(*args, **kwargs):
        output = kwargs["output"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"format": kwargs["pack_path"] if "pack_path" in kwargs else "test"}), encoding="utf-8")
        return 0

    def fake_learn_from_report(report: Path, *, policy: Path, summary: Path) -> int:
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(
            json.dumps({"tag_weights": {"attack": 1.0}, "observations": 10}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(f"learned from {report.name}\n", encoding="utf-8")
        return 0

    def fake_compare_agents_report(*args, **kwargs):
        compare_calls.append(
            {
                "candidate_weights": str(kwargs["candidate_weights"]),
                "baseline_weights": str(kwargs["baseline_weights"]),
            }
        )
        return {
            "candidate_wins": 12,
            "baseline_wins": 8,
            "candidate_win_rate": 0.6,
            "baseline_win_rate": 0.4,
        }

    monkeypatch.setattr("ygotrainingbot.cli._train_format_pack", fake_train_format_pack)
    monkeypatch.setattr("ygotrainingbot.cli._learn_from_report", fake_learn_from_report)
    monkeypatch.setattr("ygotrainingbot.cli._compare_agents_report", fake_compare_agents_report)

    output_dir = tmp_path / "curriculum-out"
    final_policy = tmp_path / "final-policy.json"
    initial_policy = tmp_path / "missing-policy.json"

    exit_code = main(
        [
            "train-format-curriculum",
            "--packs",
            str(pack_a),
            str(pack_b),
            "--edopro-home",
            str(edopro_home),
            "--current-policy",
            str(initial_policy),
            "--output-dir",
            str(output_dir),
            "--promote-to",
            str(final_policy),
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert exit_code == 0
    assert len(report["stages"]) == 2
    assert len(compare_calls) == 1
    assert compare_calls[0]["baseline_weights"].replace("\\", "/").endswith("01-goat-2005/promoted-policy.json")
    assert report["final_policy"] == str(final_policy)
    assert final_policy.exists()
