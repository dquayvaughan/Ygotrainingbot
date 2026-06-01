from ygotrainingbot.deck_quality import count_signature_hits, is_synthesized_shell, validate_tournament_deck


def test_validate_tournament_deck_rejects_placeholder_shell() -> None:
    deck = {
        "name": "Ryzeal representative top shell",
        "main": list(range(1000, 1040)),
        "extra": list(range(2000, 2015)),
        "source": "Representative Ryzeal topping shell for training; normalized to card IDs.",
    }
    issues = validate_tournament_deck(
        deck,
        archetype="Ryzeal",
        signature_ids=(34516264,),
        search_keywords=("ryzeal",),
    )
    assert any("placeholder" in issue for issue in issues)


def test_count_signature_hits() -> None:
    deck = {"main": [34516264, 34516264, 1], "extra": [34022970]}
    assert count_signature_hits(deck, (34516264, 34022970)) == 2


def test_is_synthesized_shell() -> None:
    assert is_synthesized_shell({"name": "X representative top shell", "source": "Representative X topping shell"})
