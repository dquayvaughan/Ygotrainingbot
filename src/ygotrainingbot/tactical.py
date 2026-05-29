"""Tactical board context and shallow search helpers for Phase 3."""

from __future__ import annotations

from dataclasses import dataclass

from ygotrainingbot.models import GameAction, VisibleGameState


@dataclass(frozen=True, slots=True)
class TacticalContext:
    """Lightweight public information for scoring decisions."""

    active_player: str
    active_lp: int
    opponent_lp: int
    lp_gap: int
    behind_on_lp: bool
    ahead_on_lp: bool
    opponent_low: bool
    active_low: bool


def tactical_context_from_state(state: VisibleGameState) -> TacticalContext | None:
    """Parse life points from gateway ``public_zones`` when available."""

    life_points = state.public_zones.get("life_points")
    if not life_points:
        return None

    parsed: dict[str, int] = {}
    for entry in life_points:
        text = str(entry)
        if ":" not in text:
            continue
        name, _, value = text.partition(":")
        try:
            parsed[name.strip()] = int(value.strip())
        except ValueError:
            continue

    active_lp = parsed.get(state.active_player)
    if active_lp is None:
        return None

    opponent_entries = [(name, lp) for name, lp in parsed.items() if name != state.active_player]
    if not opponent_entries:
        return None
    opponent_lp = opponent_entries[0][1]
    lp_gap = active_lp - opponent_lp
    return TacticalContext(
        active_player=state.active_player,
        active_lp=active_lp,
        opponent_lp=opponent_lp,
        lp_gap=lp_gap,
        behind_on_lp=lp_gap < -500,
        ahead_on_lp=lp_gap > 500,
        opponent_low=opponent_lp <= 2500,
        active_low=active_lp <= 2500,
    )


def tactical_action_bonus(
    context: TacticalContext,
    action: GameAction,
    legal_actions: tuple[GameAction, ...],
) -> float:
    """Adjust an action score using LP pressure and available alternatives."""

    tags = set(action.tags)
    bonus = 0.0
    has_attack = any("attack" in other.tags or "direct-attack" in other.tags for other in legal_actions)
    has_removal = any({"removal", "negate", "battle-trap", "destroy-monster"} & set(other.tags) for other in legal_actions)

    if context.behind_on_lp:
        if "attack" in tags or "direct-attack" in tags:
            bonus += 35.0
        if "normal-summon" in tags or "special-summon" in tags:
            bonus += 20.0
        if "phase" in tags and has_attack:
            bonus -= 45.0

    if context.ahead_on_lp and "phase" in tags and has_attack:
        bonus -= 25.0

    if context.opponent_low and ("attack" in tags or "direct-attack" in tags or "lethal" in tags):
        bonus += 40.0

    if context.active_low and "decline" in tags and has_removal:
        bonus -= 30.0

    if "set-spell" in tags and context.opponent_low and has_attack:
        bonus -= 15.0

    return bonus


def opponent_reply_penalty(
    context: TacticalContext | None,
    action: GameAction,
    legal_actions: tuple[GameAction, ...],
) -> float:
    """Conservative one-ply penalty for lines that invite obvious punishment."""

    tags = set(action.tags)
    penalty = 0.0
    has_removal = any({"removal", "negate", "battle-trap", "destroy-monster"} & set(other.tags) for other in legal_actions)
    has_attack = any("attack" in other.tags or "direct-attack" in other.tags for other in legal_actions)

    if "decline" in tags and has_removal:
        penalty += 40.0
    if "phase" in tags and has_attack:
        penalty += 25.0
    if context is not None and context.behind_on_lp and "phase" in tags:
        penalty += 20.0
    if context is not None and context.opponent_low and "decline" in tags and has_attack:
        penalty += 35.0
    return penalty


def follow_up_bonus(
    context: TacticalContext | None,
    action: GameAction,
    legal_actions: tuple[GameAction, ...],
) -> float:
    """Second search ply: reward lines that keep initiative after the reply."""

    tags = set(action.tags)
    bonus = 0.0
    if "lethal" in tags:
        bonus += 500.0
    if context is not None and context.opponent_low and ("attack" in tags or "direct-attack" in tags):
        bonus += 25.0
    if "removal" in tags or "destroy-monster" in tags:
        bonus += 10.0
    if "normal-summon" in tags and any("attack" in other.tags for other in legal_actions):
        bonus += 8.0
    return bonus
