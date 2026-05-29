from ygotrainingbot.deck_lists import DeckZones, is_extra_deck_monster, split_main_and_extra


def test_is_extra_deck_monster_detects_synchro() -> None:
    synchro = 0x1 | 0x2000
    normal = 0x1 | 0x10
    assert is_extra_deck_monster(synchro)
    assert not is_extra_deck_monster(normal)


def test_split_main_and_extra() -> None:
    zones = split_main_and_extra(
        [100, 200, 300],
        card_type=lambda cid: 0x2001 if cid == 200 else 0x11,
    )
    assert zones.main == (100, 300)
    assert zones.extra == (200,)


def test_deck_zones_validate_extra_limit() -> None:
    try:
        DeckZones(main=tuple(range(40)), extra=tuple(range(16))).validate()
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "15" in str(exc)
