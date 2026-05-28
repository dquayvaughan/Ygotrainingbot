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


def learn_from_report(report_path: Path, policy_path: Path | None = None) -> tuple[dict[str, Any], str]:
    """Read a training report, update policy weights, and return English feedback."""

    report = json.loads(report_path.read_text(encoding="utf-8"))
    previous = _load_policy(policy_path) if policy_path else LearnedPolicy(tag_weights={}, observations=0)
    analysis = analyze_report(report)
    learned = _update_policy(previous, analysis)
    if policy_path is not None:
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(
            json.dumps(
                {
                    "tag_weights": learned.tag_weights,
                    "observations": learned.observations,
                    "version": _next_version(previous),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "parent_observations": previous.observations,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return analysis, render_english_report(analysis, learned)


def analyze_report(report: dict[str, Any]) -> dict[str, Any]:
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
    samples: list[dict[str, Any]] = []

    for item in reports:
        wins.update({str(k): _int(v) for k, v in dict(item.get("wins_by_agent", {})).items()})
        tags.update({str(k): _int(v) for k, v in dict(item.get("tags", {})).items()})
        actions.update({str(k): _int(v) for k, v in dict(item.get("action_counts", {})).items()})
        samples.extend(dict(sample) for sample in item.get("decision_samples", []) if isinstance(sample, dict))

    best_plays = _best_play_samples(samples)
    mistakes = _mistake_samples(samples)
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


def _update_policy(previous: LearnedPolicy, analysis: dict[str, Any]) -> LearnedPolicy:
    weights = defaultdict(float, previous.tag_weights)
    total_games = max(1, int(analysis["total_games"]))
    draw_rate = int(analysis["draws"]) / total_games
    top_tags = Counter({str(tag): int(count) for tag, count in analysis["top_tags"]})

    for tag in ("attack", "direct-attack", "lethal", "removal", "destroy-monster", "battle-trap", "negate"):
        if top_tags.get(tag):
            weights[tag] += min(2.0, top_tags[tag] / max(1, int(analysis["total_decisions"])) * 10)
    if draw_rate > 0.5:
        weights["phase"] -= 0.5
        weights["decline"] -= 0.5
        weights["attack"] += 0.5
        weights["direct-attack"] += 0.5
    if top_tags.get("decline", 0) > top_tags.get("chain", 0) * 0.8 and top_tags.get("chain", 0):
        weights["decline"] -= 0.25
        weights["removal"] += 0.25
        weights["negate"] += 0.25
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
