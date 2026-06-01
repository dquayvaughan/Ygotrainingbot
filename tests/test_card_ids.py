from pathlib import Path

from ygotrainingbot.card_ids import canonicalize_card_id, edopro_card_id_aliases
from ygotrainingbot.format_training import load_format_pack


def test_bagooska_alt_art_alias() -> None:
    edopro_card_id_aliases.cache_clear()
    assert canonicalize_card_id(90590304) == 90590303


def test_ryzeal_deck_resolves_bagooska_script_id() -> None:
    edopro_card_id_aliases.cache_clear()
    pack = load_format_pack(Path("configs/format-packs/banlists/banlist-2026-01.json"))
    ryzeal = next(deck for deck in pack.decks if deck.name == "Ryzeal")
    assert 90590303 in ryzeal.extra
    assert 90590304 not in ryzeal.extra
