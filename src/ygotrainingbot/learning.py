"""Learn from simulation reports and explain the results in plain English."""

from __future__ import annotations

import ast
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ygotrainingbot.duel_logs import load_decision_samples_for_learning


IMPORTANT_TAGS = {
    "attack",
    "direct-attack",
    "lethal",
    "removal",
    "destroy-monster",
    "negate",
    "battle-trap",
    "protect",
    "draw",
    "search",
    "banish",
    "special-summon",
    "set-spell",
    "set-monster",
    "phase",
    "decline",
}


@dataclass(frozen=True, slots=True)
class LearnedPolicy:
    """Small persistent weight table learned from reports."""

    tag_weights: dict[str, float]
    observations: int


def learn_from_report(
    report_path: Path,
    policy_path: Path | None = None,
    *,
    update_scale: float = 1.0,
) -> tuple[dict[str, Any], str]:
    """Read a training report, update policy weights, and return English feedback."""

    report = json.loads(report_path.read_text(encoding="utf-8"))
    previous = _load_policy(policy_path) if policy_path else LearnedPolicy(tag_weights={}, observations=0)
    analysis = analyze_report(report, report_path=report_path)
    learned = _update_policy(previous, analysis, update_scale=update_scale)
    if policy_path is not None:
        from ygotrainingbot.policy_runtime import learned_weight_scale, write_policy_file

        policy_path.parent.mkdir(parents=True, exist_ok=True)
        write_policy_file(
            policy_path,
            learned.tag_weights,
            observations=learned.observations,
            learned_weight_scale_value=learned_weight_scale(policy_path),
            parent_observations=previous.observations,
        )
    return analysis, render_english_report(analysis, learned)


def analyze_report(report: dict[str, Any], *, report_path: Path | None = None) -> dict[str, Any]:
    """Extract performance, mistake, and best-play signals from a report."""

    reports = list(_iter_leaf_reports(report))
    total_games = _int(report.get("total_games")) or sum(_int(item.get("games")) for item in reports)
    total_decisions = _int(report.get("total_traced_decisions")) or sum(
        _int(item.get("traced_decisions")) for item in reports
    )
    draws = sum(_int(item.get("draws")) for item in reports)
    wins = Counter()
    tags = Counter()
    actions = Counter()
    samples = load_decision_samples_for_learning(report, report_path=report_path)
    if not samples:
        for item in reports:
            samples.extend(
                dict(sample)
                for sample in item.get("decision_samples", [])
                if isinstance(sample, dict)
            )

    for item in reports:
        wins.update({str(k): _int(v) for k, v in dict(item.get("wins_by_agent", {})).items()})
        tags.update({str(k): _int(v) for k, v in dict(item.get("tags", {})).items()})
        actions.update({str(k): _int(v) for k, v in dict(item.get("action_counts", {})).items()})
    if samples and total_decisions <= 0:
        total_decisions = len(samples)

    best_plays = _best_play_samples(samples)
    mistakes = _mistake_samples(samples)
    mistake_adjustments = _mistake_weight_adjustments(samples)
    outcome_adjustments = _outcome_weight_adjustments(samples)
    best_line_depth = _why_best_line_samples(samples)
    tag_lessons = _tag_lessons(tags, total_decisions)
    bottlenecks = _bottlenecks(tags, actions, draws, total_games)

    return {
        "format": report.get("format") or report.get("pack") or "unknown",
        "total_games": total_games,
        "total_decisions": total_decisions,
        "draws": draws,
        "wins": dict(wins),
        "top_tags": tags.most_common(20),
        "top_actions": actions.most_common(20),
        "best_plays": best_plays,
        "mistakes": mistakes,
        "mistake_adjustments": dict(mistake_adjustments),
        "outcome_adjustments": dict(outcome_adjustments),
        "best_line_depth": best_line_depth,
        "tag_lessons": tag_lessons,
        "bottlenecks": bottlenecks,
    }


def render_english_report(analysis: dict[str, Any], learned: LearnedPolicy) -> str:
    """Render a simple English report for humans."""

    lines = [
        f"Yu-Gi-Oh bot learning report for {analysis['format']}",
        "",
        "Performance:",
        f"- Games reviewed: {analysis['total_games']}",
        f"- Decisions reviewed: {analysis['total_decisions']}",
        f"- Draws: {analysis['draws']}",
    ]
    wins = analysis["wins"]
    if wins:
        lines.append("- Wins by agent: " + ", ".join(f"{agent}={count}" for agent, count in wins.items()))
    else:
        lines.append("- Wins by agent: none recorded")

    lines.extend(["", "What I learned:"])
    for lesson in analysis["tag_lessons"][:8]:
        lines.append(f"- {lesson}")
    if not analysis["tag_lessons"]:
        lines.append("- I need more decisive games before I can trust strong tag conclusions.")

    lines.extend(["", "Best plays I found:"])
    for play in analysis["best_plays"][:8]:
        lines.append(f"- {play}")
    if not analysis["best_plays"]:
        lines.append("- No high-confidence best plays were found yet.")

    lines.extend(["", "Likely mistakes or weak spots:"])
    for mistake in analysis["mistakes"][:8]:
        lines.append(f"- {mistake}")
    for bottleneck in analysis["bottlenecks"][:6]:
        lines.append(f"- {bottleneck}")
    if not analysis["mistakes"] and not analysis["bottlenecks"]:
        lines.append("- No obvious repeated mistake pattern was detected in this report.")

    lines.extend(["", "Why the best line was better:"])
    for insight in analysis["best_line_depth"][:10]:
        lines.append(f"- {insight}")
    if not analysis["best_line_depth"]:
        lines.append("- Not enough scored alternatives were logged to explain best-line depth yet.")

    lines.extend(["", "Policy updates saved:"])
    if learned.tag_weights:
        for tag, weight in sorted(learned.tag_weights.items(), key=lambda item: abs(item[1]), reverse=True)[:12]:
            direction = "prefer" if weight > 0 else "avoid"
            lines.append(f"- {direction} `{tag}`: learned weight {weight:+.2f}")
    else:
        lines.append("- No weight changes yet.")

    lines.extend(
        [
            "",
            "Next training target:",
            "- Run the same format again and compare whether these weight changes reduce draws and improve wins.",
            "- If chain windows still only show decline, debug why the specific trap/effect is not legally activatable.",
        ]
    )
    return "\n".join(lines) + "\n"


def _iter_leaf_reports(report: dict[str, Any]) -> Iterable[dict[str, Any]]:
    if "matchups" in report:
        for matchup in report["matchups"]:
            if not isinstance(matchup, dict):
                continue
            for key in ("report", "candidate_first", "baseline_first"):
                value = matchup.get(key)
                if isinstance(value, dict):
                    yield value
    elif "comparisons" in report:
        for comparison in report["comparisons"]:
            if isinstance(comparison, dict):
                yield from _iter_leaf_reports(comparison)
    else:
        yield report


def _best_play_samples(samples: list[dict[str, Any]]) -> list[str]:
    scored: list[tuple[float, str]] = []
    for sample in samples:
        tags = tuple(str(tag) for tag in sample.get("selected_tags", ()))
        if not IMPORTANT_TAGS.intersection(tags):
            continue
        score = _selected_score(sample.get("evaluation", ""))
        score += float(sample.get("selected_expected_value") or 0)
        if "lethal" in tags:
            score += 10_000
        if score <= 0 and not {"removal", "battle-trap", "attack", "set-spell"}.intersection(tags):
            continue
        scored.append((score, _sample_sentence(sample, score)))
    return [sentence for _score, sentence in sorted(scored, reverse=True)[:12]]


def _mistake_samples(samples: list[dict[str, Any]]) -> list[str]:
    mistakes: list[tuple[float, str]] = []
    for sample in samples:
        selected = str(sample.get("selected_action", ""))
        tags = tuple(str(tag) for tag in sample.get("selected_tags", ()))
        alternatives = _top_alternatives(sample.get("evaluation", ""))
        if alternatives and alternatives[0].get("action_id") != selected:
            gap = float(alternatives[0].get("score", 0)) - _selected_score(sample.get("evaluation", ""))
            if gap > 20:
                best_tags = _format_tags(alternatives[0].get("tags", []))
                selected_tags = _format_tags(tags)
                mistakes.append(
                    (
                        gap,
                        (
                            f"On turn {sample.get('turn')}, {sample.get('agent')} chose `{selected}` "
                            f"({selected_tags}) but `{alternatives[0].get('action_id')}` scored about {gap:.1f} "
                            f"higher with {best_tags}."
                        ),
                    )
                )
        if "phase" in tags and "to-end-phase" in selected:
            mistakes.append(
                (
                    10,
                    f"On turn {sample.get('turn')}, {sample.get('agent')} ended the phase. Check whether a summon, attack, set, or activation was available.",
                )
            )
        if "decline" in tags:
            mistakes.append(
                (
                    8,
                    f"On turn {sample.get('turn')}, {sample.get('agent')} declined a chain window. This is fine only if no tagged response was available.",
                )
            )
    return [sentence for _score, sentence in sorted(mistakes, reverse=True)[:12]]


def _mistake_weight_adjustments(samples: list[dict[str, Any]]) -> Counter[str]:
    """Turn high-confidence mistake traces into tag weight nudges."""

    adjustments: Counter[str] = Counter()
    for sample in samples:
        selected = str(sample.get("selected_action", ""))
        alternatives = _top_alternatives(sample.get("evaluation", ""))
        if not alternatives or alternatives[0].get("action_id") == selected:
            continue
        gap = float(alternatives[0].get("score", 0)) - _selected_score(sample.get("evaluation", ""))
        if gap <= 20:
            continue
        boost = min(2.5, gap / 16.0)
        penalty = min(1.25, gap / 32.0)
        for tag in alternatives[0].get("tags", []):
            adjustments[str(tag)] += boost
        for tag in sample.get("selected_tags", ()):
            adjustments[str(tag)] -= penalty
    return adjustments


def _why_best_line_samples(samples: list[dict[str, Any]]) -> list[str]:
    insights: list[tuple[float, str]] = []
    for sample in samples:
        alternatives = _top_alternatives(sample.get("evaluation", ""))
        if not alternatives:
            continue
        selected_action = str(sample.get("selected_action", ""))
        selected_score = _selected_score(sample.get("evaluation", ""))
        selected_ev = _float_or_none(sample.get("selected_expected_value"))
        best = alternatives[0]
        if str(best.get("action_id", "")) == selected_action:
            if len(alternatives) < 2:
                continue
            runner_up = alternatives[1]
            gap = float(best.get("score", 0.0)) - float(runner_up.get("score", 0.0))
            if gap <= 10:
                continue
            sentence = (
                f"Turn {sample.get('turn')} ({sample.get('summary')}): `{best.get('action_id')}` was best "
                f"because it outscored `{runner_up.get('action_id')}` by {gap:.1f}. "
                f"Winning tags: {_format_tags(best.get('tags', []))}; runner-up tags: {_format_tags(runner_up.get('tags', []))}."
            )
            insights.append((gap, sentence))
            continue

        best_score = float(best.get("score", 0.0))
        gap = best_score - selected_score
        if gap <= 12:
            continue
        best_tags = _format_tags(best.get("tags", []))
        selected_tags = _format_tags(sample.get("selected_tags", []))
        reasons = [f"score edge {gap:.1f}"]
        best_ev = _extract_expected_value(best)
        if best_ev is not None and selected_ev is not None:
            ev_gap = best_ev - selected_ev
            if abs(ev_gap) >= 0.1:
                reasons.append(f"expected value edge {ev_gap:+.1f}")
        sentence = (
            f"Turn {sample.get('turn')} ({sample.get('summary')}): best line was `{best.get('action_id')}` over "
            f"`{selected_action}` due to {', '.join(reasons)}. Best tags: {best_tags}; chosen tags: {selected_tags}."
        )
        insights.append((gap, sentence))
    return [sentence for _score, sentence in sorted(insights, reverse=True)[:12]]


def _tag_lessons(tags: Counter[str], total_decisions: int) -> list[str]:
    lessons = []
    if total_decisions <= 0:
        return lessons
    for tag, count in tags.most_common(12):
        if tag in {"edopro", "ocgcore-wasm", "max-decisions"}:
            continue
        rate = count / total_decisions
        if tag in {"removal", "battle-trap", "destroy-monster", "attack", "direct-attack", "lethal"}:
            lessons.append(f"`{tag}` appeared {count} times ({rate:.1%} of decisions); keep tracking whether it converts into wins.")
        elif tag in {"decline", "phase"} and rate > 0.2:
            lessons.append(f"`{tag}` appeared often ({count} times). This may indicate passive or stalled play.")
        else:
            lessons.append(f"`{tag}` appeared {count} times.")
    return lessons


def _bottlenecks(tags: Counter[str], actions: Counter[str], draws: int, total_games: int) -> list[str]:
    bottlenecks = []
    if total_games and draws / total_games > 0.5:
        bottlenecks.append("Too many games ended in draws. The bot needs more pressure, better battle decisions, or longer decisive tests.")
    if tags.get("decline", 0) > tags.get("chain", 0) * 0.8 and tags.get("chain", 0) > 0:
        bottlenecks.append("Most chain windows were declined. Confirm whether real chain candidates existed, then reward tagged removal/negate/protection responses.")
    if actions.get("to-end-phase", 0) > actions.get("attack-0", 0) + actions.get("normal-summon-0", 0):
        bottlenecks.append("Ending phases is outnumbering proactive attacks/summons. Penalize passive phase movement unless no useful action exists.")
    return bottlenecks


def _outcome_weight_adjustments(samples: list[dict[str, Any]]) -> Counter[str]:
    """Boost tags that correlate with wins; penalize tags that correlate with losses."""

    win_tags: Counter[str] = Counter()
    loss_tags: Counter[str] = Counter()
    second_win_tags: Counter[str] = Counter()
    second_loss_tags: Counter[str] = Counter()

    for sample in samples:
        won = sample.get("game_won")
        if won is None:
            continue
        tags = [str(tag) for tag in sample.get("selected_tags", ())]
        goes_first = sample.get("bot_goes_first")
        if won:
            win_tags.update(tags)
            if goes_first is False:
                second_win_tags.update(tags)
        else:
            loss_tags.update(tags)
            if goes_first is False:
                second_loss_tags.update(tags)

    adjustments: Counter[str] = Counter()
    for tag in set(win_tags) | set(loss_tags):
        wins = win_tags.get(tag, 0)
        losses = loss_tags.get(tag, 0)
        total = wins + losses
        if total < 3:
            continue
        win_share = wins / total
        if win_share >= 0.55:
            adjustments[tag] += min(2.0, (win_share - 0.5) * 5.0)
        elif win_share <= 0.45:
            adjustments[tag] -= min(1.5, (0.5 - win_share) * 4.0)

    for tag in set(second_win_tags) | set(second_loss_tags):
        wins = second_win_tags.get(tag, 0)
        losses = second_loss_tags.get(tag, 0)
        total = wins + losses
        if total < 2:
            continue
        win_share = wins / total
        if win_share >= 0.55:
            adjustments[tag] += min(1.5, (win_share - 0.5) * 4.0)
        elif win_share <= 0.45:
            adjustments[tag] -= min(1.0, (0.5 - win_share) * 3.0)

    return adjustments


def _update_policy(
    previous: LearnedPolicy,
    analysis: dict[str, Any],
    *,
    update_scale: float = 1.0,
) -> LearnedPolicy:
    weights = defaultdict(float, previous.tag_weights)
    total_games = max(1, int(analysis["total_games"]))
    draw_rate = int(analysis["draws"]) / total_games
    top_tags = Counter({str(tag): int(count) for tag, count in analysis["top_tags"]})
    scale = max(0.0, float(update_scale))

    for tag in ("attack", "direct-attack", "lethal", "removal", "destroy-monster", "battle-trap", "negate"):
        if top_tags.get(tag):
            weights[tag] += scale * min(5.0, top_tags[tag] / max(1, int(analysis["total_decisions"])) * 25)
    if draw_rate > 0.5:
        weights["phase"] -= scale * 1.0
        weights["decline"] -= scale * 1.0
        weights["attack"] += scale * 1.0
        weights["direct-attack"] += scale * 1.0
    if top_tags.get("decline", 0) > top_tags.get("chain", 0) * 0.8 and top_tags.get("chain", 0):
        weights["decline"] -= scale * 0.75
        weights["removal"] += scale * 0.75
        weights["negate"] += scale * 0.75

    for source in ("mistake_adjustments", "outcome_adjustments"):
        tag_adjustments = analysis.get(source, {})
        if isinstance(tag_adjustments, dict):
            for tag, delta in tag_adjustments.items():
                weights[str(tag)] += scale * float(delta)

    return LearnedPolicy(tag_weights=dict(sorted(weights.items())), observations=previous.observations + int(analysis["total_decisions"]))


def _next_version(previous: LearnedPolicy) -> int:
    return max(1, previous.observations + 1)


def _load_policy(policy_path: Path | None) -> LearnedPolicy:
    if policy_path is None or not policy_path.exists():
        return LearnedPolicy(tag_weights={}, observations=0)
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return LearnedPolicy(
        tag_weights={str(k): float(v) for k, v in dict(payload.get("tag_weights", {})).items()},
        observations=int(payload.get("observations", 0)),
    )


def _sample_sentence(sample: dict[str, Any], score: float) -> str:
    return (
        f"On turn {sample.get('turn')}, {sample.get('agent')} chose `{sample.get('selected_action')}` "
        f"({sample.get('selected_label')}) with tags {sample.get('selected_tags')} and score {score:.1f}."
    )


def _selected_score(evaluation: object) -> float:
    match = re.search(r"selected_score=([-0-9.]+)", str(evaluation))
    return float(match.group(1)) if match else 0.0


def _top_alternatives(evaluation: object) -> list[dict[str, Any]]:
    text = str(evaluation)
    marker = "top_alternatives="
    if marker not in text:
        return []
    try:
        value = ast.literal_eval(text.split(marker, 1)[1])
    except (SyntaxError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _extract_expected_value(action_entry: dict[str, Any]) -> float | None:
    label = str(action_entry.get("label", ""))
    match = re.search(r"EV=([-0-9.]+)", label)
    return float(match.group(1)) if match else None


def _float_or_none(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_tags(tags: Iterable[object]) -> str:
    normalized = [str(tag) for tag in tags if str(tag)]
    if not normalized:
        return "no key tags"
    return ", ".join(normalized[:4])


def _int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
