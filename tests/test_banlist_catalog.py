from pathlib import Path

from ygotrainingbot.banlist_catalog import banlist_periods, top5_for_period
from ygotrainingbot.deck_visual import card_image_url, deck_to_visual
from ygotrainingbot.format_pack_generator import build_format_pack, write_format_packs


def test_banlist_catalog_covers_2010_through_2026() -> None:
    periods = banlist_periods()
    assert len(periods) >= 32
    assert periods[0].period_id == "2010-03"
    assert periods[-1].year >= 2025
    for period in periods:
        assert len(period.top5) == 5


def test_top5_for_edison_period() -> None:
    top5 = top5_for_period("2010-03")
    assert "Quickdraw Dandywarrior" in top5
    assert "Frog Monarch" in top5


def test_generated_banlist_pack_has_five_decks() -> None:
    repo = Path.cwd()
    period = banlist_periods()[0]
    payload = build_format_pack(period, repo_root=repo)
    assert len(payload["decks"]) == 5
    assert payload["banlist_period"] == "2010-03"
    for deck in payload["decks"]:
        assert len(deck["main"]) >= 40
        assert len(deck["extra"]) <= 15
        assert len(deck["side"]) <= 15
        assert "source" in deck


def test_write_format_packs_creates_files(tmp_path: Path) -> None:
    repo = Path.cwd()
    out = tmp_path / "packs"
    paths = write_format_packs(repo, output_dir=out)
    assert len(paths) == len(banlist_periods())
    assert paths[0].is_file()


def test_deck_to_visual_groups_duplicates() -> None:
    visual = deck_to_visual(
        {
            "name": "Test",
            "archetype": "Test",
            "main": [123, 123, 456] + [789] * 37,
            "extra": [],
        }
    )
    assert visual["main_count"] == 40
    counts = {entry["id"]: entry["count"] for entry in visual["main"]}
    assert counts[123] == 2
    assert counts[789] == 37
    assert card_image_url(123).endswith("/123.jpg")
