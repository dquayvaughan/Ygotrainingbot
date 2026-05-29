from ygotrainingbot.agents import ShallowSearchAgent, create_agent
from ygotrainingbot.models import GameAction, VisibleGameState
from ygotrainingbot.tactical import (
    TacticalContext,
    tactical_action_bonus,
    tactical_context_from_state,
)


def test_tactical_context_parses_life_points() -> None:
    state = VisibleGameState(
        state_id="s1",
        turn=3,
        active_player="bot-01",
        summary="Battle",
        legal_actions=(),
        public_zones={
            "life_points": ("bot-01:4200", "bot-02:6800"),
        },
    )
    context = tactical_context_from_state(state)
    assert context is not None
    assert context.active_lp == 4200
    assert context.opponent_lp == 6800
    assert context.behind_on_lp is True


def test_search_agent_attacks_when_behind_on_lp() -> None:
    end_phase = GameAction("to-end-phase", "Go to End Phase", tags=("phase",))
    attack = GameAction(
        "attack-0",
        "Attack",
        tags=("attack", "direct-attack", "damage:1500"),
    )
    state = VisibleGameState(
        state_id="battle",
        turn=4,
        active_player="bot-01",
        summary="Battle phase",
        legal_actions=(end_phase, attack),
        public_zones={"life_points": ("bot-01:3000", "bot-02:6500")},
    )
    agent = create_agent("search-control", "bot-01")
    assert isinstance(agent, ShallowSearchAgent)
    assert agent.choose_action(state) == attack


def test_search_agent_prefers_removal_over_decline() -> None:
    decline = GameAction("decline", "Do not chain", tags=("decline", "chain"))
    removal = GameAction("chain-0", "Activate trap", tags=("removal", "battle-trap", "negate"))
    state = VisibleGameState(
        state_id="chain",
        turn=2,
        active_player="bot-01",
        summary="Chain window",
        legal_actions=(decline, removal),
        public_zones={"life_points": ("bot-01:2500", "bot-02:7000")},
    )
    assert create_agent("search-control").choose_action(state) == removal


def test_tactical_bonus_penalizes_stalling_when_behind() -> None:
    context = TacticalContext(
        active_player="bot-01",
        active_lp=3000,
        opponent_lp=6500,
        lp_gap=-3500,
        behind_on_lp=True,
        ahead_on_lp=False,
        opponent_low=False,
        active_low=False,
    )
    phase = GameAction("to-end-phase", "End", tags=("phase",))
    attack = GameAction("attack-0", "Attack", tags=("attack",))
    bonus = tactical_action_bonus(context, phase, (phase, attack))
    assert bonus < 0
