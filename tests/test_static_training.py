import json
from pathlib import Path

from ygotrainingbot.cli import main
from ygotrainingbot.data import build_card_sets, card_from_ygoprodeck, load_card_database
from ygotrainingbot.models import CardType
from ygotrainingbot.static_training import StaticSetTrainer, effect_tags_for


RAW_CARDS = [
    {
        "id": 1,
        "name": "Starter Synchron",
        "type": "Effect Monster",
        "desc": "If this card is Normal Summoned: add 1 Synchron card from your Deck.",
        "archetype": "Synchron",
        "card_sets": [{"set_name": "Training Dawn", "set_code": "TDN-001"}],
    },
    {
        "id": 2,
        "name": "Synchron Burst",
        "type": "Spell Card",
        "desc": "Special Summon 1 Synchron monster from your GY.",
        "archetype": "Synchron",
        "card_sets": [{"set_name": "Training Dawn", "set_code": "TDN-002"}],
    },
    {
        "id": 3,
        "name": "Backrow Check",
        "type": "Trap Card",
        "desc": "When your opponent activates a card effect: negate that activation.",
        "card_sets": [{"set_name": "Training Dawn", "set_code": "TDN-003"}],
    },
]


def test_ygoprodeck_cards_are_grouped_into_sets() -> None:
    card = card_from_ygoprodeck(RAW_CARDS[1])

    assert card.name == "Synchron Burst"
    assert card.card_type == CardType.SPELL
    assert card.archetypes == ("Synchron",)
    assert effect_tags_for(card) == ("graveyard", "special-summon")

    card_sets = build_card_sets(RAW_CARDS)

    assert len(card_sets) == 1
    assert card_sets[0].code == "TDN"
    assert card_sets[0].name == "Training Dawn"
    assert card_sets[0].card_names() == (
        "Starter Synchron",
        "Synchron Burst",
        "Backrow Check",
    )


def test_static_trainer_mines_profiles_and_interaction_candidates() -> None:
    report = StaticSetTrainer().train(build_card_sets(RAW_CARDS))

    assert report.sets_analyzed == 1
    assert report.cards_analyzed == 3
    assert report.set_profiles[0].top_archetypes == (("Synchron", 2),)
    assert any(candidate.shared_signals == ("Synchron",) for candidate in report.interaction_candidates)


def test_cli_runs_static_training_from_cache(tmp_path: Path, capsys) -> None:
    cache = tmp_path / "cards.json"
    cache.write_text(json.dumps({"data": RAW_CARDS}), encoding="utf-8")

    exit_code = main(["train-static", "--cache", str(cache), "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["sets_analyzed"] == 1
    assert payload["cards_analyzed"] == 3
    assert load_card_database(cache) == RAW_CARDS
