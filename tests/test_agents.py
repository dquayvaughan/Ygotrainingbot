from ygotrainingbot.agents import AggressiveHeuristicAgent, HeuristicActionAgent, RandomLegalActionAgent, create_agent
from ygotrainingbot.models import GameAction, SelectCardPrompt, VisibleGameState


def test_heuristic_agent_prefers_proactive_action_over_phase_end() -> None:
    end_phase = GameAction("to-end-phase", "Go to End Phase", tags=("phase",))
    summon = GameAction("normal-summon-0", "Normal Summon Hunter Spider", tags=("normal-summon",))
    state = VisibleGameState(
        state_id="main-phase",
        turn=1,
        active_player="bot-a",
        summary="Main phase decision",
        legal_actions=(end_phase, summon),
    )

    action = HeuristicActionAgent().choose_action(state)

    assert action == summon


def test_create_agent_builds_known_policies() -> None:
    assert create_agent("first-legal", "a").name == "a"
    assert create_agent("heuristic", "b").name == "b"
    assert create_agent("search-control", "c").name == "c"


def test_aggressive_agent_prefers_lethal_attack() -> None:
    end_phase = GameAction("to-end-phase", "Go to End Phase", tags=("phase",))
    attack = GameAction(
        "attack-0",
        "Direct attack with Hunter Spider for 1500",
        expected_value=15.0,
        tags=("attack", "direct-attack", "damage:1500", "lethal"),
    )
    state = VisibleGameState(
        state_id="battle-phase",
        turn=5,
        active_player="bot-a",
        summary="Battle phase lethal decision",
        legal_actions=(end_phase, attack),
    )

    assert AggressiveHeuristicAgent().choose_action(state) == attack


def test_random_agent_is_seeded() -> None:
    actions = (
        GameAction("a", "A"),
        GameAction("b", "B"),
        GameAction("c", "C"),
    )
    state = VisibleGameState(
        state_id="random",
        turn=1,
        active_player="bot-a",
        summary="Random choice",
        legal_actions=actions,
    )

    first = RandomLegalActionAgent(seed=7).choose_action(state)
    second = RandomLegalActionAgent(seed=7).choose_action(state)

    assert first == second


def test_create_agent_builds_benchmark_policies() -> None:
    for policy in ["random", "aggressive", "tempo", "control"]:
        assert create_agent(policy, policy).name == policy


def test_control_agent_prefers_removal_chain_over_decline() -> None:
    decline = GameAction("decline-chain", "Do not chain", tags=("chain", "decline"))
    removal = GameAction(
        "chain-0",
        "Chain Sakuretsu Armor",
        expected_value=3.5,
        tags=("chain", "trap", "removal", "destroy-monster", "battle-trap"),
    )
    state = VisibleGameState(
        state_id="attack-response",
        turn=4,
        active_player="bot-b",
        summary="Opponent declared an attack.",
        legal_actions=(decline, removal),
    )

    assert create_agent("control").choose_action(state) == removal


def test_heuristic_agent_picks_highest_scored_select_card() -> None:
    pot = GameAction(
        "select-card-0",
        "Select Pot of Prosperity",
        tags=("select-card", "spell", "banish", "removal"),
    )
    cyclone = GameAction(
        "select-card-1",
        "Select Cosmic Cyclone",
        tags=("select-card", "spell", "banish", "removal"),
    )
    state = VisibleGameState(
        state_id="select-card",
        turn=3,
        active_player="bot-a",
        summary="EDOPro select_card decision | pick 1 from: Pot of Prosperity, Cosmic Cyclone",
        legal_actions=(pot, cyclone),
        select_card=SelectCardPrompt(
            pick_count=1,
            min_picks=1,
            max_picks=1,
            can_cancel=False,
            cards=((0, "Pot of Prosperity"), (1, "Cosmic Cyclone")),
        ),
    )

    assert HeuristicActionAgent().choose_action(state) == pot


def test_heuristic_agent_uses_combo_actions_for_multi_pick() -> None:
    combo = GameAction(
        "select-card-combo-0-1",
        "Select Card A, Card B",
        tags=("select-card",),
    )
    single = GameAction(
        "select-card-1",
        "Select Card B",
        tags=("select-card",),
    )
    state = VisibleGameState(
        state_id="select-multi",
        turn=2,
        active_player="bot-a",
        summary="pick 2",
        legal_actions=(combo, single),
        select_card=SelectCardPrompt(
            pick_count=2,
            min_picks=2,
            max_picks=2,
            can_cancel=False,
            cards=((0, "Card A"), (1, "Card B")),
        ),
    )

    assert HeuristicActionAgent().choose_action(state) == combo


def test_learned_weights_affect_heuristic_scoring() -> None:
    phase = GameAction("to-end-phase", "Go to End Phase", tags=("phase",))
    summon = GameAction("normal-summon-0", "Normal Summon", tags=("normal-summon",))
    only_phase = VisibleGameState(
        state_id="learned-phase",
        turn=1,
        active_player="bot-a",
        summary="Learned weights on phase",
        legal_actions=(phase,),
    )
    mixed = VisibleGameState(
        state_id="learned",
        turn=1,
        active_player="bot-a",
        summary="Learned weights",
        legal_actions=(phase, summon),
    )

    agent = create_agent("heuristic", learned_weights={"phase": 300.0})

    assert agent.choose_action(only_phase) == phase
    assert agent.choose_action(mixed) == summon
