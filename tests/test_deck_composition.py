from ygotrainingbot.deck_composition import (
    effective_deck_for_bo3_game,
    normalize_deck_dict,
    strip_extra_monsters_from_main,
)
from ygotrainingbot.format_training import FormatDeck


def test_strip_extra_monsters_from_main() -> None:
    main = (44330098, 50321796, 26202165)
    cleaned = strip_extra_monsters_from_main(main)
    assert 50321796 not in cleaned
    assert 44330098 in cleaned


def test_normalize_deck_dict_builds_side_and_extra() -> None:
    payload = normalize_deck_dict(
        {
            "name": "Test",
            "archetype": "Test",
            "main": [44330098] * 40,
            "extra": [],
            "side": [],
        },
        modern=False,
    )
    assert 40 <= len(payload["main"]) <= 60
    assert len(payload["extra"]) <= 15
    assert len(payload["side"]) <= 15


def test_normalize_deck_dict_can_preserve_tournament_lists() -> None:
    payload = normalize_deck_dict(
        {
            "name": "Tournament",
            "archetype": "Ryzeal",
            "main": list(range(1000, 1040)),
            "extra": list(range(2000, 2014)),
            "side": list(range(3000, 3014)),
        },
        pad_zones=False,
        require_side=False,
    )
    assert len(payload["main"]) == 40
    assert len(payload["extra"]) == 14
    assert len(payload["side"]) == 14


def test_effective_deck_for_bo3_swaps_side() -> None:
    deck = FormatDeck(
        name="Test",
        main=tuple(range(1000, 1055)),
        extra=(),
        side=tuple(range(2000, 2015)),
        archetype="Test",
    )
    game_two = effective_deck_for_bo3_game(deck, 2)
    assert game_two.main != deck.main
    assert len(game_two.main) == 55
    assert game_two.main[-15:] == deck.side
