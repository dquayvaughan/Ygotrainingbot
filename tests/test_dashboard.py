import json
from pathlib import Path

import pytest

from ygotrainingbot.dashboard import DashboardSettings, DashboardState


def test_dashboard_lists_format_packs(tmp_path: Path) -> None:
    state = DashboardState(
        DashboardSettings(
            repo_root=Path.cwd(),
            jobs_dir=tmp_path / "jobs",
            edopro_home=tmp_path / "edopro",
            gateway_script=Path.cwd() / "gateways/edopro-ocgcore/gateway.mjs",
        )
    )

    packs = state.format_packs()

    assert {pack["name"] for pack in packs} >= {"goat-2005", "edison-2010"}


def test_dashboard_rejects_pack_paths_outside_format_pack_dir(tmp_path: Path) -> None:
    state = DashboardState(
        DashboardSettings(
            repo_root=Path.cwd(),
            jobs_dir=tmp_path / "jobs",
            edopro_home=tmp_path / "edopro",
            gateway_script=Path.cwd() / "gateways/edopro-ocgcore/gateway.mjs",
        )
    )

    with pytest.raises(ValueError, match="configs/format-packs"):
        state.start_job("README.md", games_per_matchup=1, max_decisions=1)


def test_dashboard_job_metadata_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = DashboardState(
        DashboardSettings(
            repo_root=Path.cwd(),
            jobs_dir=tmp_path / "jobs",
            edopro_home=tmp_path / "edopro",
            gateway_script=Path.cwd() / "gateways/edopro-ocgcore/gateway.mjs",
        )
    )

    class ImmediateThread:
        def __init__(self, target, args, daemon):  # noqa: ANN001
            self.target = target
            self.args = args

        def start(self) -> None:
            return None

    monkeypatch.setattr("ygotrainingbot.dashboard.threading.Thread", ImmediateThread)

    job = state.start_job(
        "configs/format-packs/goat-2005.json",
        games_per_matchup=1,
        max_decisions=2,
    )
    metadata = state.job(job.job_id)

    assert metadata["pack"] == "configs/format-packs/goat-2005.json"
    assert metadata["status"] == "queued"
    assert json.loads((tmp_path / "jobs" / job.job_id / "meta.json").read_text())["job_id"] == job.job_id
