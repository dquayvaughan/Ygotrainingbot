import json
from pathlib import Path

import pytest

from ygotrainingbot.dashboard import DashboardSettings, DashboardState, _parse_multipart_files, _parse_multipart_form
from ygotrainingbot.paths import TrainingPaths
from ygotrainingbot.ydk import read_ydk, write_ydk


def _dashboard_settings(
    tmp_path: Path,
    *,
    data_dir: Path | None = None,
    human_catalog_dir: Path | None = None,
) -> DashboardSettings:
    root = data_dir or tmp_path / "data"
    paths = TrainingPaths.resolve(
        Path.cwd(),
        data_dir=root,
        edopro_home=tmp_path / "edopro",
        human_catalog_dir=human_catalog_dir or tmp_path / "human-duels",
    )
    return DashboardSettings.from_training_paths(
        paths,
        gateway_script=Path.cwd() / "gateways/edopro-ocgcore/gateway.mjs",
    )


def test_dashboard_lists_roster_bots() -> None:
    state = DashboardState(_dashboard_settings(Path.cwd() / ".ygotrain/test-jobs-roster"))
    bots = state.roster_bots(None)
    assert any(bot["bot_id"] == "bot-01" and bot["name"] == "Yugi" for bot in bots)


def test_dashboard_start_bot_spar_job_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = DashboardState(_dashboard_settings(tmp_path))

    class ImmediateThread:
        def __init__(self, target, args, daemon):  # noqa: ANN001
            pass

        def start(self) -> None:
            return None

    monkeypatch.setattr("ygotrainingbot.dashboard.threading.Thread", ImmediateThread)

    job = state.start_training(
        {
            "job_kind": "bot-spar",
            "bot_id": "bot-01",
            "opponent_bot_id": "bot-02",
            "pack": "configs/format-packs/banlists/banlist-2010-03.json",
            "deck_name": "pack:Frog Monarch representative top shell",
            "opponent_deck_name": "pack:Quickdraw Dandywarrior representative top shell",
            "games_per_matchup": 2,
            "max_decisions": 600,
        }
    )
    meta = state.job(job.job_id)
    assert meta["job_kind"] == "bot-spar"
    assert meta["bot_id"] == "bot-01"
    assert meta["deck_name"] == "Frog Monarch representative top shell"


def test_dashboard_lists_format_packs(tmp_path: Path) -> None:
    state = DashboardState(_dashboard_settings(tmp_path))

    packs = state.format_packs()

    assert {pack["name"] for pack in packs} >= {"goat-2005", "banlist-2010-03"}
    edison = next(p for p in packs if p["name"] == "banlist-2010-03")
    assert edison["decks"]
    assert any("Frog" in deck["archetype"] for deck in edison["decks"])


def test_dashboard_rejects_pack_paths_outside_format_pack_dir(tmp_path: Path) -> None:
    state = DashboardState(_dashboard_settings(tmp_path))

    with pytest.raises(ValueError, match="configs/format-packs"):
        state.start_job("README.md", games_per_matchup=1, max_decisions=600)


def test_dashboard_job_metadata_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = DashboardState(_dashboard_settings(tmp_path))

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
        max_decisions=600,
    )
    metadata = state.job(job.job_id)

    assert metadata["pack"] == "configs/format-packs/goat-2005.json"
    assert metadata["job_kind"] == "format-pack"
    assert metadata["status"] == "queued"
    assert json.loads((tmp_path / "data" / "jobs" / job.job_id / "meta.json").read_text())["job_id"] == job.job_id


def _dashboard_state(tmp_path: Path) -> DashboardState:
    return DashboardState(_dashboard_settings(tmp_path, human_catalog_dir=tmp_path / "human-duels"))


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
    assert "bot_stats" in result
    assert (tmp_path / "data" / "bot-training-stats.json").is_file()


def test_dashboard_upload_yrpx_replay(tmp_path: Path) -> None:
    from tests.test_replay_convert import _build_yrpx_bytes

    state = _dashboard_state(tmp_path)
    payload = state.upload_human_replays(
        [("duel.yrpX", _build_yrpx_bytes())],
        study_agent="alice",
        format_name="banlist-test",
    )
    assert payload["imported"] == 1
    assert payload["converted_from_replay"] == 1
    assert payload["catalog"]["duel_count"] == 1
    assert payload["bot_stats"]["bots"]


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


def test_parse_multipart_form_fields_and_ydk() -> None:
    body = (
        b"--abc\r\n"
        b'Content-Disposition: form-data; name="bot_id"\r\n\r\n'
        b"bot-01\r\n"
        b"--abc\r\n"
        b'Content-Disposition: form-data; name="deck_name"\r\n\r\n'
        b"My Deck\r\n"
        b"--abc\r\n"
        b'Content-Disposition: form-data; name="ydk"; filename="deck.ydk"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        b"#main\r\n123\r\n"
        b"--abc--\r\n"
    )
    fields, files = _parse_multipart_form(body, "multipart/form-data; boundary=abc")
    assert fields["bot_id"] == "bot-01"
    assert fields["deck_name"] == "My Deck"
    assert files[0][0] == "deck.ydk"


def test_dashboard_match_setup_includes_meta_and_gauntlet() -> None:
    state = _dashboard_state(Path.cwd() / ".ygotrain/test-match-setup")
    setup = state.match_setup(
        train_bot_id="bot-01",
        opponent_bot_id="bot-02",
        pack_path="configs/format-packs/banlists/banlist-2010-03.json",
    )
    assert setup["train_decks"]
    assert setup["opponent_decks"][0]["id"] == "all"
    assert any(deck["source"] == "meta" for deck in setup["train_decks"])


def test_dashboard_training_bootstrap() -> None:
    state = _dashboard_state(Path.cwd() / ".ygotrain/test-bootstrap")
    payload = state.training_bootstrap()
    assert payload["format_packs"]
    assert payload["banlists"]
    assert payload["bots"]
    assert payload["opponent_options"]
    assert any(bot["bot_id"] == "bot-01" for bot in payload["bots"])


def test_dashboard_banlist_meta_gallery() -> None:
    state = _dashboard_state(Path.cwd() / ".ygotrain/test-gallery")
    gallery = state.banlist_meta_gallery("configs/format-packs/banlists/banlist-2010-03.json")
    assert len(gallery["decks"]) == 5
    assert gallery["decks"][0]["main"]
    assert gallery["decks"][0]["main"][0]["image_url"]


def test_dashboard_import_ydk_deck(tmp_path: Path) -> None:
    state = DashboardState(_dashboard_settings(tmp_path))
    ydk_path = tmp_path / "custom.ydk"
    write_ydk(ydk_path, [100000 + index for index in range(40)], extra=[200001, 200002])
    payload = state.import_ydk_deck(
        bot_id="bot-01",
        deck_name="Test Import Deck",
        ydk_bytes=ydk_path.read_bytes(),
        filename="custom.ydk",
    )
    assert payload["deck_id"].startswith("bot-01-")
    assert payload["main"]
    decks = state.match_setup(
        train_bot_id="bot-01",
        opponent_bot_id="bot-02",
        pack_path="configs/format-packs/banlists/banlist-2010-03.json",
    )
    assert any(item["id"] == f"custom:{payload['deck_id']}" for item in decks["train_decks"])
