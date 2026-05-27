from ygotrainingbot.agents import HeuristicActionAgent, create_agent
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
