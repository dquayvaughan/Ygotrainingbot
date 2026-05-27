from ygotrainingbot import (
    Card,
    CardSet,
    CardType,
    Deck,
    DeterministicScenarioSimulator,
    FirstLegalActionAgent,
    GameAction,
    SelfPlayRunner,
    SetExplorationPlan,
    TraceCoach,
    VisibleGameState,
)


def test_self_play_runner_summarizes_deterministic_scenario() -> None:
    card = Card(card_id="lob-001", name="Blue-Eyes White Dragon", card_type=CardType.MONSTER)
    card_set = CardSet(code="LOB", name="Legend of Blue Eyes White Dragon", cards=(card,))
    deck = Deck(name="starter", main=(card,))
    first = FirstLegalActionAgent("kaiba")
    second = FirstLegalActionAgent("yugi")
    summon = GameAction("summon-blue-eyes", "Summon Blue-Eyes", tags=("pressure",))
    set_pass = GameAction("set-pass", "Set and pass", tags=("defensive",))
    state = VisibleGameState(
        state_id="turn-1-main",
        turn=1,
        active_player="kaiba",
        summary="Kaiba can pressure immediately or play defensively.",
        legal_actions=(summon, set_pass),
    )
    simulator = DeterministicScenarioSimulator(states=(state,), winner="kaiba", tags=("lob",))
    plan = SetExplorationPlan(card_set=card_set, decks=(deck, deck), repetitions=3)

    report, results = SelfPlayRunner(simulator).run(plan, first, second)

    assert report.card_set_code == "LOB"
    assert report.matches_played == 3
    assert report.total_decisions == 3
    assert report.wins_by_agent == {"kaiba": 3}
    assert report.recurring_tags == {"pressure": 3, "lob": 3}
    assert len(results) == 3


def test_trace_coach_recommends_better_expected_value_action() -> None:
    greedy = GameAction("attack", "Attack now", expected_value=0.1)
    safer = GameAction("bait-response", "Bait the response first", expected_value=0.7)
    state = VisibleGameState(
        state_id="chain-window",
        turn=4,
        active_player="student",
        summary="Opponent has one set back-row before battle phase.",
        legal_actions=(greedy, safer),
    )
    simulator = DeterministicScenarioSimulator(states=(state,), winner=None)
    result = simulator.play(FirstLegalActionAgent("student"), FirstLegalActionAgent("opponent"))

    recommendations = TraceCoach().recommend(result)

    assert len(recommendations) == 1
    assert recommendations[0].title == "Consider Bait the response first"
    assert "projected better" in recommendations[0].recommendation
