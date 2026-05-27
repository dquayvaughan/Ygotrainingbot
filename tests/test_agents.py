from ygotrainingbot.agents import AggressiveHeuristicAgent, HeuristicActionAgent, RandomLegalActionAgent, create_agent
from ygotrainingbot.models import GameAction, VisibleGameState


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
