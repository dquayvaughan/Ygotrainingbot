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

from ygotrainingbot.duel_logs import (
    end_reason_stats_from_report,
    format_gateway_health_lines,
    gateway_health_from_report,
    load_decision_samples_for_learning,
    tempo_stats_from_report,
)


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

_STATE_DESCRIPTOR_TAG_PREFIXES = ("opp-lp:", "damage:", "lp-swing:")

_NON_POLICY_TAGS = frozenset(
    {
        "monster",
        "zone",
        "select-card",
        "chain",
        "edopro",
        "ocgcore-wasm",
        "max-decisions",
    }
)

# Duel-length targets used when applying tempo nudges (engine turns from game logs).
TEMPO_SLOW_AVG_TURNS = 10.0
TEMPO_FAST_WIN_TURNS = 8
TEMPO_SLOW_WIN_TURNS = 12


def is_state_descriptor_tag(tag: str) -> bool:
    """Return True for board-state tags that should not become learned weights."""

    return any(tag.startswith(prefix) for prefix in _STATE_DESCRIPTOR_TAG_PREFIXES)


def is_learnable_policy_tag(tag: str) -> bool:
    """Return True when a tag represents a strategic preference worth persisting."""

    if is_state_descriptor_tag(tag):
        return False
    return tag not in _NON_POLICY_TAGS


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
    failed_games = _collect_failed_games(report)
    failed_count = len(failed_games)
    total_games = _int(report.get("total_games")) or sum(_int(item.get("games")) for item in reports)
    completed_games = max(total_games - failed_count, 0)
    total_decisions = _int(report.get("total_traced_decisions")) or sum(
        _int(item.get("traced_decisions")) for item in reports
    )
    draws = sum(_int(item.get("draws")) for item in reports)
    sim_faults = sum(_int(item.get("sim_faults")) for item in reports)
    stale_turn_limits = sum(_int(item.get("stale_turn_limit")) for item in reports)
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

    healthy_samples = _samples_for_learning(samples)
    retry_stuck_games = _count_end_reason_games(samples, "retry_stuck")
    capped_games = _count_end_reason_games(samples, "max_decisions")
    stale_turn_limit_games = _count_stale_turn_limit_games(samples)
    best_plays = _dedupe_sentences(_best_play_samples(healthy_samples))
    mistakes = _dedupe_sentences(_mistake_samples(healthy_samples))
    mistake_adjustments = _mistake_weight_adjustments(healthy_samples)
    outcome_adjustments = _outcome_weight_adjustments(healthy_samples)
    best_line_depth = _dedupe_sentences(_why_best_line_samples(healthy_samples))
    tag_lessons = _tag_lessons(tags, total_decisions)
    tempo = tempo_stats_from_report(report, report_path=report_path)
    end_reasons = end_reason_stats_from_report(report, report_path=report_path)
    gateway_health = gateway_health_from_report(report, report_path=report_path)
    tempo_adjustments = _tempo_weight_adjustments(tempo)
    bottlenecks = _bottlenecks(
        tags,
        actions,
        draws,
        completed_games or total_games,
        tempo=tempo,
        sim_faults=sim_faults,
        stale_turn_limit_games=stale_turn_limit_games,
    )
    failure_summaries = _summarize_failure_errors(failed_games)

    return {
        "format": report.get("format") or report.get("pack") or "unknown",
        "total_games": total_games,
        "completed_games": completed_games,
        "failed_games": failed_count,
        "failure_summaries": failure_summaries,
        "total_decisions": total_decisions,
        "draws": draws,
        "sim_faults": sim_faults,
        "stale_turn_limits": stale_turn_limits,
        "wins": dict(wins),
        "tempo": tempo,
        "end_reasons": end_reasons,
        "top_tags": tags.most_common(20),
        "top_actions": actions.most_common(20),
        "best_plays": best_plays,
        "mistakes": mistakes,
        "mistake_adjustments": dict(mistake_adjustments),
        "outcome_adjustments": dict(outcome_adjustments),
        "tempo_adjustments": tempo_adjustments,
        "best_line_depth": best_line_depth,
        "tag_lessons": tag_lessons,
        "bottlenecks": bottlenecks,
        "retry_stuck_games": retry_stuck_games,
        "capped_games": capped_games,
        "stale_turn_limit_games": stale_turn_limit_games,
        "gateway_health": gateway_health,
    }

NON_LEARNING_END_REASONS = frozenset(
    {"retry_stuck", "max_decisions", "engine_stall"},
)


def _life_points_tied(sample: dict[str, Any]) -> bool:
    life_points = sample.get("life_points")
    if not isinstance(life_points, (list, tuple)) or len(life_points) != 2:
        return False
    try:
        return int(life_points[0]) == int(life_points[1])
    except (TypeError, ValueError):
        return False


def _count_stale_turn_limit_games(samples: list[dict[str, Any]]) -> int:
    game_ids = {
        str(sample.get("game_log_path") or sample.get("game_id") or id(sample))
        for sample in samples
        if sample.get("end_reason") == "turn_limit" and _life_points_tied(sample)
    }
    return len(game_ids)


def _samples_for_learning(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        sample
        for sample in samples
        if sample.get("end_reason") not in NON_LEARNING_END_REASONS
        and not (
            sample.get("end_reason") == "turn_limit" and _life_points_tied(sample)
        )
    ]


def _samples_excluding_retry_stuck(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _samples_for_learning(samples)


def _count_end_reason_games(samples: list[dict[str, Any]], *reasons: str) -> int:
    reason_set = set(reasons)
    game_ids = {
        str(sample.get("game_log_path") or sample.get("game_id") or id(sample))
        for sample in samples
        if sample.get("end_reason") in reason_set
    }
    return len(game_ids)


def render_english_report(analysis: dict[str, Any], learned: LearnedPolicy) -> str:
    """Render a simple English report for humans."""

    failed_games = int(analysis.get("failed_games") or 0)
    completed_games = int(analysis["completed_games"])
    lines = [
        f"Yu-Gi-Oh bot learning report for {analysis['format']}",
        "",
        "Performance:",
        f"- Games scheduled: {analysis['total_games']}",
        f"- Games completed: {completed_games}",
    ]
    if failed_games:
        lines.append(f"- Games failed: {failed_games}")
    lines.extend(
        [
            f"- Decisions reviewed: {analysis['total_decisions']}",
            f"- Draws: {analysis['draws']}",
        ]
    )
    sim_faults = int(analysis.get("sim_faults") or 0)
    stale_turn_limits = int(analysis.get("stale_turn_limits") or 0)
    if sim_faults:
        lines.append(f"- Simulation faults (retry_stuck, max_decisions): {sim_faults}")
    if stale_turn_limits:
        lines.append(
            f"- Turn-limit stalemates (8000–8000 LP, no winner): {stale_turn_limits}"
        )
    tempo = dict(analysis.get("tempo") or {})
    if tempo:
        if "avg_duel_turns" in tempo:
            lines.append(
                f"- Average duel length: {tempo['avg_duel_turns']:.1f} engine turns "
                f"(competitive games usually end by turn 6)."
            )
        if "avg_decisions_per_game" in tempo:
            lines.append(f"- Average bot decisions per game: {tempo['avg_decisions_per_game']:.1f}")
        if "avg_decisions_per_duel_turn" in tempo:
            lines.append(
                f"- Average decisions per duel turn: {tempo['avg_decisions_per_duel_turn']:.1f} "
                "(each card prompt counts as one decision)."
            )
    wins = analysis["wins"]
    if wins:
        lines.append("- Wins by agent: " + ", ".join(f"{agent}={count}" for agent, count in wins.items()))
    else:
        lines.append("- Wins by agent: none recorded")

    end_reasons = dict(analysis.get("end_reasons") or {})
    if end_reasons:
        parts = ", ".join(f"{reason}={count}" for reason, count in sorted(end_reasons.items(), key=lambda item: -item[1]))
        lines.append(f"- Duel endings: {parts}")
        retry_stuck = int(end_reasons.get("retry_stuck", 0))
        completed = int(analysis.get("completed_games") or analysis.get("total_games") or 0)
        if completed and retry_stuck >= max(1, completed // 2):
            lines.append(
                "- Many games ended on `retry_stuck` (the gateway exhausted legal idle/chain/select responses the engine kept rejecting). "
                "Restart the dashboard after updating `gateways/edopro-ocgcore/gateway.mjs`. "
                "These games do not count as real wins and were excluded from best-play learning."
            )

    lines.extend(format_gateway_health_lines(dict(analysis.get("gateway_health") or {})))

    failure_summaries = list(analysis.get("failure_summaries") or ())
    if failed_games and completed_games == 0 and not analysis["total_decisions"]:
        lines.extend(["", "Gateway failures (no duels finished):"])
        for summary in failure_summaries[:5]:
            lines.append(f"- {summary}")
        if len(failure_summaries) > 5:
            lines.append(f"- …and {len(failure_summaries) - 5} more distinct error(s).")
        lines.append(
            "- Learning did not run because the gateway never produced duel traces. "
            "Fix the deck/script error above, then rerun this job."
        )

    retry_stuck_games = int(analysis.get("retry_stuck_games") or 0)
    capped_games = int(analysis.get("capped_games") or 0)
    mostly_retry_stuck = completed_games > 0 and retry_stuck_games >= max(1, completed_games // 2)
    mostly_capped = completed_games > 0 and capped_games >= max(1, completed_games // 2)
    stale_turn_limit_games = int(analysis.get("stale_turn_limit_games") or 0)
    mostly_stale_turn_limit = (
        completed_games > 0 and stale_turn_limit_games >= max(1, completed_games // 2)
    )

    lines.extend(["", "What I learned:"])
    if mostly_retry_stuck:
        lines.append(
            "- Learning is paused for this run: most games ended on `retry_stuck`, so tag weights and best plays would be noise."
        )
        lines.append(
                "- Fix gateway retry handling (idlecmd, chain, zone placement), rerun, then review wins and duel length."
        )
    elif mostly_capped:
        lines.append(
            "- Learning is down-weighted: most games hit the decision cap (long combos in one engine turn). "
            "Prefer real `lp` endings; combo blowouts should end on `max_decisions` only."
        )
    else:
        for lesson in analysis["tag_lessons"][:8]:
            lines.append(f"- {lesson}")
        if not analysis["tag_lessons"]:
            if failed_games and completed_games == 0:
                lines.append("- No learning signals were recorded because every scheduled game failed before finishing.")
            else:
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
    tempo_adj = dict(analysis.get("tempo_adjustments") or {})
    if tempo_adj:
        boosts = ", ".join(f"`{tag}` {delta:+.2f}" for tag, delta in sorted(tempo_adj.items(), key=lambda item: -abs(item[1]))[:6])
        lines.append(f"- Applied tempo weight nudges (long duels): {boosts}")
    if not analysis["mistakes"] and not analysis["bottlenecks"] and not tempo_adj:
        lines.append("- No obvious repeated mistake pattern was detected in this report.")

    lines.extend(["", "Why the best line was better:"])
    for insight in analysis["best_line_depth"][:10]:
        lines.append(f"- {insight}")
    if not analysis["best_line_depth"]:
        lines.append("- Not enough scored alternatives were logged to explain best-line depth yet.")

    lines.extend(["", "Policy updates saved:"])
    if mostly_retry_stuck:
        lines.append("- Skipped meaningful weight updates because `retry_stuck` dominated this job.")
    elif mostly_capped:
        lines.append(
            "- Skipped meaningful weight updates because decision-cap aborts dominated this job."
        )
    elif mostly_stale_turn_limit:
        lines.append(
            "- Skipped meaningful weight updates because turn-limit stalemates dominated this job."
        )
    else:
        policy_tags = [
            (tag, weight)
            for tag, weight in sorted(learned.tag_weights.items(), key=lambda item: abs(item[1]), reverse=True)
            if is_learnable_policy_tag(tag)
        ]
        if policy_tags:
            for tag, weight in policy_tags[:12]:
                direction = "prefer" if weight > 0 else "avoid"
                lines.append(f"- {direction} `{tag}`: learned weight {weight:+.2f}")
        else:
            lines.append("- No weight changes yet.")

    lines.extend(["", "Next training target:"])
    if mostly_retry_stuck:
        lines.append("- From repo root: `cd gateways/edopro-ocgcore && npm install && node patch-ocgcore-select.mjs`")
        lines.append("- Restart the dashboard job for this banlist after the patch reports list encoding active.")
    elif failed_games and completed_games == 0:
        lines.append("- Fix the gateway/deck validation errors listed above, then rerun this exact matchup.")
        lines.append("- Open the job report.json failed_games list if you need the full stderr for each game.")
    else:
        lines.append(
            "- Run the same format again and compare whether these weight changes reduce draws and improve wins."
        )
        lines.append(
            "- If chain windows still only show decline, debug why the specific trap/effect is not legally activatable."
        )
    return "\n".join(lines) + "\n"


def _collect_failed_games(report: dict[str, Any]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    raw = report.get("failed_games")
    if isinstance(raw, list):
        failed.extend(item for item in raw if isinstance(item, dict))
    for item in _iter_leaf_reports(report):
        nested = item.get("failed_games")
        if isinstance(nested, list):
            failed.extend(entry for entry in nested if isinstance(entry, dict))
    return failed


def _summarize_failure_errors(failed_games: list[dict[str, Any]]) -> list[str]:
    if not failed_games:
        return []
    counts: Counter[str] = Counter()
    for entry in failed_games:
        error = str(entry.get("error") or "unknown error").strip()
        counts[_normalize_failure_error(error)] += 1
    return [
        f"{count} game(s): {message}"
        for message, count in counts.most_common(8)
    ]


def _normalize_failure_error(error: str) -> str:
    match = re.search(r"no supported selectable message was emitted: (.+)$", error, flags=re.MULTILINE)
    if match:
        payload = match.group(1).strip()
        try:
            messages = json.loads(payload)
        except json.JSONDecodeError:
            messages = None
        if isinstance(messages, list) and messages:
            message = messages[0]
            message_type = message.get("type")
            if message_type == 25:
                cards = message.get("cards") or []
                if cards:
                    codes = ", ".join(str(card.get("code")) for card in cards[:3])
                    return f"Unhandled hand sort prompt for card(s) {codes} (gateway SORT_CARD support required)"
                return "Unhandled hand sort prompt (gateway SORT_CARD support required)"
        return f"Unsupported ocgcore prompt: {payload[:180]}"
    match = re.search(r"Deck script validation failed: (.+)$", error, flags=re.MULTILINE)
    if match:
        return f"Deck script validation failed ({match.group(1).strip()})"
    match = re.search(r"Error: (.+)$", error, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    first_line = error.splitlines()[0].strip()
    return first_line[:240] if first_line else "unknown error"


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
    scored: list[tuple[float, str, tuple[object, ...]]] = []
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
        dedupe_key = (
            sample.get("agent"),
            sample.get("selected_action"),
            tags,
        )
        scored.append((score, _sample_sentence(sample, score), dedupe_key))
    scored.sort(key=lambda item: item[0], reverse=True)
    sentences: list[str] = []
    seen_keys: set[tuple[object, ...]] = set()
    for _score, sentence, dedupe_key in scored:
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        sentences.append(sentence)
        if len(sentences) >= 12:
            break
    return sentences


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
                            f"{_format_decision_context(sample)}, {sample.get('agent')} chose "
                            f"{_action_display(selected, sample.get('selected_label'))} "
                            f"({selected_tags}) but "
                            f"{_action_display(alternatives[0].get('action_id'), alternatives[0].get('label'))} "
                            f"scored about {gap:.1f} higher with {best_tags}."
                        ),
                    )
                )
        if "phase" in tags and "to-end-phase" in selected:
            mistakes.append(
                (
                    10,
                    f"{_format_decision_context(sample)}, {sample.get('agent')} ended the phase. Check whether a summon, attack, set, or activation was available.",
                )
            )
        if "decline" in tags:
            mistakes.append(
                (
                    8,
                    f"{_format_decision_context(sample)}, {sample.get('agent')} declined a chain window. This is fine only if no tagged response was available.",
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
            if not _score_gap_is_meaningful(
                gap,
                best.get("tags", []),
                runner_up.get("tags", []),
            ):
                continue
            sentence = (
                f"{_format_decision_context(sample)} ({sample.get('summary')}): "
                f"{_action_display(best.get('action_id'), best.get('label'))} was best "
                f"because it outscored {_action_display(runner_up.get('action_id'), runner_up.get('label'))} "
                f"by {gap:.1f}. "
                f"Winning tags: {_format_tags(best.get('tags', []))}; runner-up tags: {_format_tags(runner_up.get('tags', []))}."
            )
            insights.append((gap, sentence))
            continue

        best_score = float(best.get("score", 0.0))
        gap = best_score - selected_score
        if not _score_gap_is_meaningful(
            gap,
            best.get("tags", []),
            sample.get("selected_tags", []),
        ):
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
            f"{_format_decision_context(sample)} ({sample.get('summary')}): best line was "
            f"{_action_display(best.get('action_id'), best.get('label'))} over "
            f"{_action_display(selected_action, sample.get('selected_label'))} due to {', '.join(reasons)}. "
            f"Best tags: {best_tags}; chosen tags: {selected_tags}."
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


def _bottlenecks(
    tags: Counter[str],
    actions: Counter[str],
    draws: int,
    total_games: int,
    *,
    tempo: dict[str, float] | None = None,
    sim_faults: int = 0,
    stale_turn_limit_games: int = 0,
) -> list[str]:
    bottlenecks = []
    tempo = tempo or {}
    if sim_faults >= max(1, total_games // 2):
        bottlenecks.append(
            "Most games ended on retry_stuck or hit max_decisions before a competitive finish. "
            "Fix gateway prompts before tuning battle policy."
        )
        return bottlenecks
    if stale_turn_limit_games >= max(1, total_games // 2):
        avg_decisions = float(tempo.get("avg_decisions_per_game", 0.0))
        bottlenecks.append(
            "Most games timed out at max_duel_turns with no LP change (draws). "
            f"Bots are stalling — only ~{avg_decisions:.0f} main-phase decisions per game on average. "
            "Restart the gateway (main-phase auto-pass disabled) and rerun; check decks open with summons."
        )
        return bottlenecks
    avg_duel_turns = float(tempo.get("avg_duel_turns", 0.0))
    if avg_duel_turns > 10:
        bottlenecks.append(
            f"Duels are averaging {avg_duel_turns:.1f} engine turns. Reward faster pressure, "
            "lethal lines, and fewer end-phase passes so games finish closer to turn 6."
        )
    avg_decisions_per_turn = float(tempo.get("avg_decisions_per_duel_turn", 0.0))
    if avg_decisions_per_turn > 35:
        bottlenecks.append(
            f"Each duel turn averages {avg_decisions_per_turn:.1f} bot decisions. "
            "Long combo chains are expected in modern decks, but avoid ending phases while summons or activations are still available."
        )
    if total_games and draws / total_games > 0.5:
        bottlenecks.append("Too many games ended in draws. The bot needs more pressure, better battle decisions, or longer decisive tests.")
    if tags.get("decline", 0) > tags.get("chain", 0) * 0.8 and tags.get("chain", 0) > 0:
        bottlenecks.append("Most chain windows were declined. Confirm whether real chain candidates existed, then reward tagged removal/negate/protection responses.")
    if actions.get("to-end-phase", 0) > actions.get("attack-0", 0) + actions.get("normal-summon-0", 0):
        bottlenecks.append("Ending phases is outnumbering proactive attacks/summons. Penalize passive phase movement unless no useful action exists.")
    return bottlenecks


def _sample_outcome_weight(sample: dict[str, Any], *, won: bool) -> float:
    """Scale tag credit by duel length when game_turns is known."""

    turns = _int(sample.get("game_turns"))
    if turns <= 0:
        return 1.0
    if won:
        if turns <= TEMPO_FAST_WIN_TURNS:
            return 2.0
        if turns <= TEMPO_SLOW_WIN_TURNS:
            return 1.0
        return 0.35
    if turns > TEMPO_SLOW_WIN_TURNS:
        return 1.25
    return 1.0


def _weighted_tag_hits(counter: Counter[str], tags: list[str], amount: float) -> None:
    if amount <= 0:
        return
    whole = int(amount)
    fraction = amount - whole
    for tag in tags:
        if whole:
            counter[tag] += whole
        if fraction >= 0.5:
            counter[tag] += 1


def _tempo_weight_adjustments(tempo: dict[str, float] | None) -> dict[str, float]:
    """Return tag deltas when average duel length exceeds the competitive target."""

    tempo = tempo or {}
    avg_turns = float(tempo.get("avg_duel_turns", 0.0))
    avg_decisions_per_turn = float(tempo.get("avg_decisions_per_duel_turn", 0.0))
    if avg_turns <= TEMPO_SLOW_AVG_TURNS and avg_decisions_per_turn <= 35:
        return {}

    strength = 0.0
    if avg_turns > TEMPO_SLOW_AVG_TURNS:
        strength = max(strength, min(2.0, (avg_turns - TEMPO_SLOW_AVG_TURNS) / 5.0))
    if avg_decisions_per_turn > 35:
        strength = max(strength, min(1.5, (avg_decisions_per_turn - 35.0) / 20.0))

    return {
        "attack": 0.5 * strength,
        "direct-attack": 0.5 * strength,
        "lethal": 0.75 * strength,
        "special-summon": 0.3 * strength,
        "normal-summon": 0.25 * strength,
        "phase": -0.5 * strength,
        "decline": -0.4 * strength,
        "set-spell": -0.25 * strength,
        "set-monster": -0.15 * strength,
    }


def _outcome_weight_adjustments(samples: list[dict[str, Any]]) -> Counter[str]:
    """Boost tags that correlate with wins; penalize tags that correlate with losses."""

    win_tags: Counter[str] = Counter()
    loss_tags: Counter[str] = Counter()
    second_win_tags: Counter[str] = Counter()
    second_loss_tags: Counter[str] = Counter()

    for sample in samples:
        if sample.get("end_reason") in NON_LEARNING_END_REASONS:
            continue
        won = sample.get("game_won")
        if won is None:
            continue
        tags = [
            str(tag)
            for tag in sample.get("selected_tags", ())
            if is_learnable_policy_tag(str(tag))
        ]
        goes_first = sample.get("bot_goes_first")
        weight = _sample_outcome_weight(sample, won=bool(won))
        if won:
            _weighted_tag_hits(win_tags, tags, weight)
            if goes_first is False:
                _weighted_tag_hits(second_win_tags, tags, weight)
        else:
            _weighted_tag_hits(loss_tags, tags, weight)
            if goes_first is False:
                _weighted_tag_hits(second_loss_tags, tags, weight)

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
    weights = defaultdict(
        float,
        {
            tag: value
            for tag, value in previous.tag_weights.items()
            if is_learnable_policy_tag(tag)
        },
    )
    total_games = max(1, int(analysis["total_games"]))
    draw_rate = int(analysis["draws"]) / total_games
    top_tags = Counter({str(tag): int(count) for tag, count in analysis["top_tags"]})
    scale = max(0.0, float(update_scale))

    retry_stuck_games = int(analysis.get("retry_stuck_games") or 0)
    capped_games = int(analysis.get("capped_games") or 0)
    completed = max(1, int(analysis.get("completed_games") or analysis["total_games"]))
    skip_frequency_bumps = (
        retry_stuck_games >= max(1, completed // 2)
        or capped_games >= max(1, completed // 2)
    )
    if not skip_frequency_bumps:
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

    for source in ("mistake_adjustments", "outcome_adjustments", "tempo_adjustments"):
        tag_adjustments = analysis.get(source, {})
        if isinstance(tag_adjustments, dict):
            for tag, delta in tag_adjustments.items():
                if not is_learnable_policy_tag(str(tag)):
                    continue
                weights[str(tag)] += scale * float(delta)

    return LearnedPolicy(
        tag_weights=dict(sorted(weights.items())),
        observations=previous.observations + int(analysis["total_decisions"]),
    )


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


def _dedupe_sentences(sentences: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for sentence in sentences:
        if sentence in seen:
            continue
        seen.add(sentence)
        unique.append(sentence)
    return unique


def _action_display(action_id: object, label: object | None = None) -> str:
    """Show both gateway action id and human label (card name when available)."""

    aid = str(action_id or "")
    lab = str(label or "").strip()
    if lab:
        return f"`{aid}` ({lab})"
    return f"`{aid}`"


def _format_decision_context(sample: dict[str, Any]) -> str:
    duel_turn = sample.get("duel_turn")
    decision_index = sample.get("decision_index")
    legacy_turn = sample.get("turn")
    if duel_turn is not None and decision_index is not None:
        return f"On duel turn {duel_turn}, decision {decision_index}"
    if decision_index is not None:
        return f"On decision {decision_index}"
    if legacy_turn is not None:
        return f"On decision {legacy_turn}"
    return "During the duel"


def _sample_sentence(sample: dict[str, Any], score: float) -> str:
    return (
        f"{_format_decision_context(sample)}, {sample.get('agent')} chose "
        f"{_action_display(sample.get('selected_action'), sample.get('selected_label'))} "
        f"with tags {sample.get('selected_tags')} and score {score:.1f}."
    )


def _score_gap_is_meaningful(gap: float, *tag_groups: Iterable[object]) -> bool:
    if gap <= 0:
        return False
    tags = {str(tag) for group in tag_groups for tag in group}
    if tags & {"lethal", "direct-attack", "attack"}:
        return gap >= 2.0
    return gap >= 10.0


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
