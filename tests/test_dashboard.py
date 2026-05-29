import json
from pathlib import Path

import pytest

from ygotrainingbot.dashboard import DashboardSettings, DashboardState, _parse_multipart_files


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


def _dashboard_state(tmp_path: Path) -> DashboardState:
    return DashboardState(
        DashboardSettings(
            repo_root=Path.cwd(),
            jobs_dir=tmp_path / "jobs",
            edopro_home=tmp_path / "edopro",
            gateway_script=Path.cwd() / "gateways/edopro-ocgcore/gateway.mjs",
            human_catalog_dir=tmp_path / "human-duels",
        )
    )


def test_dashboard_upload_and_learn_from_human_replays(tmp_path: Path) -> None:
    state = _dashboard_state(tmp_path)
    duel = {
        "meta": {"format": "gui-test", "study_agent": "pro"},
        "decisions": [
            {
                "turn": 1,
                "agent": "pro",
                "selected_action": "attack-0",
                "selected_tags": ["attack"],
                "evaluation": "selected_score=100.00; top_alternatives=[]",
            }
        ],
    }
    payload = state.upload_human_replays([("duel.json", json.dumps(duel).encode("utf-8"))])
    assert payload["imported"] == 1
    assert payload["catalog"]["duel_count"] == 1

    result = state.learn_from_human_replays(study_agent="pro", format_filter="gui-test")
    assert result["total_games"] == 1
    assert result["total_decisions"] == 1
    assert "human:gui-test" in result["summary"]
    assert (tmp_path / "human-duels" / "learning-summary.txt").is_file()
    assert state._global_policy_path().is_file()


def test_parse_multipart_files() -> None:
    body = (
        b"--abc\r\n"
        b'Content-Disposition: form-data; name="files"; filename="duel.json"\r\n'
        b"Content-Type: application/json\r\n\r\n"
        b'{"meta":{"format":"x"},"decisions":[{"turn":1,"agent":"a","selected_action":"x"}]}\r\n'
        b"--abc--\r\n"
    )
    files = _parse_multipart_files(body, 'multipart/form-data; boundary=abc')
    assert len(files) == 1
    assert files[0][0] == "duel.json"
    assert b'"format":"x"' in files[0][1]
