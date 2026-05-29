import json
from pathlib import Path

from ygotrainingbot.edopro_watch import _timeline_entries, export_edopro_watch_bundle


def test_timeline_entries_reads_flat_game_log_traces() -> None:
    game = {
        "traces": [
            {
                "turn": 1,
                "agent": "bot-01",
                "summary": "Main phase",
                "selected_action": "set-spell-2",
                "selected_label": "Set Mirror Force",
                "selected_tags": ["set-spell", "trap", "removal"],
            }
        ]
    }
    entries = _timeline_entries(game)
    assert len(entries) == 1
    assert entries[0]["agent"] == "bot-01"
    assert entries[0]["label"] == "Set Mirror Force"
    assert entries[0]["action_id"] == "set-spell-2"


def test_export_edopro_watch_bundle_writes_action_labels(tmp_path: Path) -> None:
    bracket = tmp_path / "bracket"
    year = 2010
    game_log = bracket / str(year) / "games" / "bot-01_vs_bot-02" / "series-000" / "game-01.json"
    pack_dir = bracket / str(year) / "packs"
    pack_dir.mkdir(parents=True)
    (pack_dir / "bot-01.json").write_text(
        json.dumps({"decks": [{"name": "Deck A", "main": [1] * 40}]}) + "\n",
        encoding="utf-8",
    )
    (pack_dir / "bot-02.json").write_text(
        json.dumps({"decks": [{"name": "Deck B", "main": [2] * 40}]}) + "\n",
        encoding="utf-8",
    )
    game_log.parent.mkdir(parents=True)
    game_log.write_text(
        json.dumps(
            {
                "meta": {
                    "year": year,
                    "home_bot_id": "bot-01",
                    "away_bot_id": "bot-02",
                    "home_name": "Yugi",
                    "away_name": "Joey",
                    "game_number": 1,
                    "goes_first": "bot-01",
                    "duel_seed": [1, 2, 3, 4],
                },
                "result": {"winner": "bot-01", "end_reason": "lp"},
                "traces": [
                    {
                        "turn": 1,
                        "agent": "bot-01",
                        "summary": "Main phase",
                        "selected_action": "attack-0",
                        "selected_label": "Attack with Monster",
                        "selected_tags": ["attack"],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    export_edopro_watch_bundle(game_log, tmp_path / "watch")
    timeline = (tmp_path / "watch" / "yugi-vs-joey-game-1" / "action-timeline.txt").read_text(encoding="utf-8")
    assert "Attack with Monster" in timeline
    assert "bot-01" in timeline
