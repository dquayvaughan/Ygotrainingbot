from __future__ import annotations

from unittest.mock import patch

from ygotrainingbot.ygoprodeck_decks import (
    deck_from_api_payload,
    scrape_deck_page,
    search_keywords,
    signature_card_ids,
)


SAMPLE_HTML = """
<div id="main_deck">
  <a data-card="20932152"></a>
  <a data-card="15341821"></a>
</div>
<div id="extra_deck">
  <a data-card="50321796"></a>
</div>
<div id="side_deck">
  <a data-card="24508291"></a>
</div>
"""


def test_signature_card_ids_from_templates() -> None:
    ids = signature_card_ids("Quickdraw Dandywarrior")
    assert 20932152 in ids


def test_search_keywords_splits_archetype_name() -> None:
    assert "quickdraw" in search_keywords("Quickdraw Dandywarrior")
    assert "snake" in search_keywords("Fire King Snake-Eye")


def test_deck_from_api_payload_parses_zones() -> None:
    deck = deck_from_api_payload(
        {
            "deck_name": "Ryzeal",
            "format": "Tournament Meta Decks",
            "tournamentName": "Richmond WCQ Regional",
            "tournamentPlacement": "Top 8",
            "username": "Pilot",
            "main_deck": '["1","2"]',
            "extra_deck": '["3"]',
            "side_deck": '["4"]',
            "pretty_url": "ryzeal-test-1",
            "deckNum": 123,
        },
        archetype="Ryzeal",
    )
    assert deck.main == (1, 2)
    assert deck.extra == (3,)
    assert deck.side == (4,)
    assert "Richmond WCQ Regional" in deck.source


def test_scrape_deck_page_parses_card_ids() -> None:
    with patch("ygotrainingbot.ygoprodeck_decks._request_text", return_value=SAMPLE_HTML):
        deck = scrape_deck_page("quickdraw-dandy-edison-deck-655819")
    assert deck.main == (20932152, 15341821)
    assert deck.extra == (50321796,)
    assert deck.side == (24508291,)
