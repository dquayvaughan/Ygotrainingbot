"""Tests for dashboard job cancellation."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ygotrainingbot.dashboard import DashboardSettings, DashboardState
from ygotrainingbot.paths import TrainingPaths


def _dashboard_settings(tmp_path: Path) -> DashboardSettings:
    paths = TrainingPaths.resolve(
        tmp_path,
        data_dir=tmp_path / "data",
        edopro_home=tmp_path / "edopro",
        human_catalog_dir=tmp_path / "human-duels",
    )
    return DashboardSettings.from_training_paths(
        paths,
        gateway_script=tmp_path / "gateway.mjs",
    )


def test_kill_all_jobs_cancels_running_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = DashboardState(_dashboard_settings(tmp_path))
    job_id = "job-running-1"
    job_dir = state.settings.jobs_dir / job_id
    job_dir.mkdir(parents=True)
    meta = {
        "job_id": job_id,
        "job_kind": "format-pack",
        "label": "test",
        "status": "running",
        "games_per_matchup": 1,
        "max_decisions": 0,
        "created_at": time.time(),
        "started_at": time.time(),
        "finished_at": None,
        "returncode": None,
        "log_path": "training.log",
        "report_path": "report.json",
        "summary_path": "learning-summary.txt",
        "policy_path": "learned-policy.json",
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    class FakeProcess:
        pid = 4242

        def poll(self) -> None:
            return None

    state._running_processes[job_id] = FakeProcess()  # type: ignore[assignment]
    monkeypatch.setattr("ygotrainingbot.dashboard._terminate_process_tree", lambda _process: None)

    result = state.kill_all_jobs()

    assert result["count"] == 1
    assert result["killed"] == [job_id]
    updated = state.job(job_id)
    assert updated["status"] == "cancelled"
    assert updated["error"] == "Stopped by user"
