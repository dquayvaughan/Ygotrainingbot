from __future__ import annotations

from pathlib import Path

from ygotrainingbot.paths import TrainingPaths


def test_training_paths_default_local_layout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("YGOTRAIN_DATA_DIR", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = TrainingPaths.resolve(repo)
    assert paths.data_dir == (repo / ".ygotrain").resolve()
    assert paths.jobs_dir == paths.data_dir / "jobs"
    assert paths.human_catalog_dir == (repo / "data" / "human-duels").resolve()
    assert paths.bracket_output_dir == (repo / "data").resolve()


def test_training_paths_centralized_data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YGOTRAIN_DATA_DIR", str(tmp_path / "fly-data"))
    paths = TrainingPaths.resolve(tmp_path / "repo")
    assert paths.data_dir == (tmp_path / "fly-data").resolve()
    assert paths.human_catalog_dir == paths.data_dir / "human-duels"
    assert paths.bracket_output_dir == paths.data_dir / "bracket"
    assert paths.edopro_home == paths.data_dir / "edopro-home"
