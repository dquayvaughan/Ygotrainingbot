from ygotrainingbot.decks import sanitize_training_deck


def test_sanitize_training_deck_preserves_size_and_uses_normals() -> None:
    main = tuple(range(40, 80))
    sanitized = sanitize_training_deck(main)
    assert len(sanitized) == 40
    assert all(card in {1184620, 3134241, 19159413, 75390004} for card in sanitized)


def test_sanitize_training_deck_elite_profile_uses_elite_pool() -> None:
    main = tuple(range(40, 80))
    sanitized = sanitize_training_deck(main, profile="elite")
    assert len(sanitized) == 40
    assert all(card in {19159413, 75390004} for card in sanitized)
