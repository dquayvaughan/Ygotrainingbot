"""Persist full duel traces and engine logs for training."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ygotrainingbot.models import DuelTrace, MatchResult


def trace_to_dict(trace: DuelTrace) -> dict[str, Any]:
    return {
        "agent": trace.agent_name,
        "note": trace.note,
        "turn": trace.state.turn,
        "duel_turn": trace.state.turn,
        "decision_index": trace.state.decision_index,
        "state_id": trace.state.state_id,
        "active_player": trace.state.active_player,
        "summary": trace.state.summary,
        "public_zones": {key: list(value) for key, value in trace.state.public_zones.items()},
        "selected_action": trace.action.action_id,
        "selected_label": trace.action.label,
        "selected_tags": list(trace.action.tags),
        "selected_expected_value": trace.action.expected_value,
        "legal_actions": [
            {
                "action_id": action.action_id,
                "label": action.label,
                "tags": list(action.tags),
                "expected_value": action.expected_value,
            }
            for action in trace.state.legal_actions
        ],
    }


def build_game_log_payload(*, meta: dict[str, Any], result: MatchResult) -> dict[str, Any]:
    metadata = dict(result.metadata)
    tags = list(result.tags)
    if metadata.get("end_reason") == "lp":
        tags = ["lp" if str(tag) == "deckout" else str(tag) for tag in tags]
    return {
        "meta": meta,
        "result": {
            "winner": result.winner,
            "loser": result.loser,
            "turns": result.turns,
            "tags": tags,
            "end_reason": metadata.get("end_reason"),
            "life_points": metadata.get("life_points"),
            "decisions": metadata.get("decisions"),
            "script_stats": metadata.get("script_stats"),
        },
        "traces": [trace_to_dict(trace) for trace in result.traces],
        "engine_logs": list(metadata.get("gateway_logs", ())),
    }


def write_game_log(path: Path, *, meta: dict[str, Any], result: MatchResult) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_game_log_payload(meta=meta, result=result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def game_log_path_for_series(
    games_dir: Path,
    *,
    year: int,
    home_bot_id: str,
    away_bot_id: str,
    series_index: int,
    game_number: int,
) -> Path:
    matchup_dir = games_dir / str(year) / "games" / f"{home_bot_id}_vs_{away_bot_id}"
    return matchup_dir / f"series-{series_index:03d}" / f"game-{game_number:02d}.json"


def trace_dict_to_decision_sample(trace: dict[str, Any]) -> dict[str, Any]:
    decision_index = trace.get("decision_index")
    duel_turn = trace.get("duel_turn", trace.get("turn"))
    legacy_turn = trace.get("turn")
    return {
        "turn": legacy_turn,
        "duel_turn": duel_turn,
        "decision_index": decision_index if decision_index is not None else legacy_turn,
        "agent": trace.get("agent"),
        "summary": trace.get("summary"),
        "selected_action": trace.get("selected_action"),
        "selected_label": trace.get("selected_label"),
        "selected_tags": list(trace.get("selected_tags", ())),
        "selected_expected_value": trace.get("selected_expected_value"),
        "public_zones": dict(trace.get("public_zones", {})),
        "evaluation": trace.get("note", ""),
    }


def samples_from_game_log(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return samples_from_game_log_payload(payload)


def samples_from_game_log_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    meta = dict(payload.get("meta", {}))
    result = dict(payload.get("result", {}))
    winner = str(result.get("winner") or "")
    goes_first = str(meta.get("goes_first") or "")
    game_turns = _int(result.get("turns"))

    end_reason = str(result.get("end_reason") or "")

    def annotate(sample: dict[str, Any]) -> dict[str, Any]:
        agent = str(sample.get("agent") or "")
        if winner and agent and end_reason not in {
            "retry_stuck",
            "max_decisions",
        }:
            sample["game_won"] = agent == winner
        if goes_first and agent:
            sample["bot_goes_first"] = goes_first == agent
        if game_turns > 0:
            sample["game_turns"] = game_turns
        if end_reason:
            sample["end_reason"] = end_reason
        life_points = result.get("life_points")
        if isinstance(life_points, (list, tuple)):
            sample["life_points"] = list(life_points)
        return sample

    traces = payload.get("traces", ())
    if isinstance(traces, list) and traces:
        return [
            annotate(trace_dict_to_decision_sample(trace))
            for trace in traces
            if isinstance(trace, dict)
        ]
    decisions = payload.get("decisions")
    if isinstance(decisions, list):
        return [
            trace_dict_to_decision_sample(_decision_dict_to_trace(item))
            for item in decisions
            if isinstance(item, dict)
        ]
    return []


def _decision_dict_to_trace(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent": decision.get("agent"),
        "turn": decision.get("turn"),
        "summary": decision.get("summary", ""),
        "note": decision.get("evaluation", decision.get("note", "")),
        "selected_action": decision.get("selected_action"),
        "selected_label": decision.get("selected_label"),
        "selected_tags": list(decision.get("selected_tags") or ()),
        "selected_expected_value": decision.get("selected_expected_value"),
        "public_zones": dict(decision.get("public_zones") or {}),
        "legal_actions": list(decision.get("legal_actions") or ()),
    }


def resolve_game_log_path(path: Path, *, report_path: Path | None = None) -> Path:
    if path.is_file():
        return path
    if report_path is not None:
        for parent in (report_path.parent, *report_path.parents):
            candidate = parent / path
            if candidate.is_file():
                return candidate
    return path


def collect_game_log_paths(report: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()

    def add(raw: object) -> None:
        if raw is None:
            return
        text = str(raw)
        if not text or text in seen:
            return
        seen.add(text)
        paths.append(Path(text))

    add(report.get("game_log_path"))
    raw_paths = report.get("game_log_paths")
    human_paths = report.get("human_duel_paths")
    if isinstance(raw_paths, list):
        for item in raw_paths:
            add(item)
    if isinstance(human_paths, list):
        for item in human_paths:
            add(item)

    matchups = report.get("matchups")
    if isinstance(matchups, list):
        for matchup in matchups:
            if not isinstance(matchup, dict):
                continue
            nested = matchup.get("report")
            if isinstance(nested, dict):
                paths.extend(collect_game_log_paths(nested))

    bot_reports = report.get("bot_training_reports")
    if isinstance(bot_reports, dict):
        for nested in bot_reports.values():
            if isinstance(nested, dict):
                paths.extend(collect_game_log_paths(nested))

    league_report = report.get("league_training_report")
    if isinstance(league_report, dict):
        paths.extend(collect_game_log_paths(league_report))

    seasons = report.get("seasons")
    if isinstance(seasons, list):
        for season in seasons:
            if isinstance(season, dict):
                paths.extend(collect_game_log_paths(season))

    games_root = report.get("games_log_root")
    if games_root and not paths:
        root = Path(str(games_root))
        if root.is_dir():
            paths.extend(sorted(root.glob("**/game-*.json")))

    return paths


def _inline_decision_samples(
    report: dict[str, Any],
    *,
    bot_agent: str | None,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    raw_samples = report.get("decision_samples")
    if isinstance(raw_samples, list):
        for sample in raw_samples:
            if not isinstance(sample, dict):
                continue
            if bot_agent is not None and str(sample.get("agent")) != bot_agent:
                continue
            samples.append(dict(sample))
    return samples


def load_decision_samples_for_learning(
    report: dict[str, Any],
    *,
    report_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Load every decision trace for learning, preferring persisted game logs."""

    bot_agent = report.get("bot_agent")
    bot_agent_str = str(bot_agent) if bot_agent is not None else None
    log_paths = collect_game_log_paths(report)
    if log_paths:
        samples: list[dict[str, Any]] = []
        for raw_path in log_paths:
            path = resolve_game_log_path(raw_path, report_path=report_path)
            if not path.is_file():
                continue
            for sample in samples_from_game_log(path):
                if bot_agent_str is not None and str(sample.get("agent")) != bot_agent_str:
                    continue
                samples.append(sample)
        if samples:
            return samples

    inline = _inline_decision_samples(report, bot_agent=bot_agent_str)
    if inline:
        return inline

    nested_samples: list[dict[str, Any]] = []
    matchups = report.get("matchups")
    if isinstance(matchups, list):
        for matchup in matchups:
            if not isinstance(matchup, dict):
                continue
            nested = matchup.get("report")
            if isinstance(nested, dict):
                nested_samples.extend(
                    load_decision_samples_for_learning(nested, report_path=report_path)
                )
    return nested_samples


def iter_game_log_paths_under(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return ()
    return sorted(root.glob("**/game-*.json"))


def end_reason_stats_from_report(
    report: dict[str, Any],
    *,
    report_path: Path | None = None,
) -> dict[str, int]:
    """Count how duels ended (lp, retry_stuck, deckout, etc.) from persisted game logs."""

    counts: dict[str, int] = {}
    for raw_path in collect_game_log_paths(report):
        path = resolve_game_log_path(raw_path, report_path=report_path)
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = dict(payload.get("result", {}))
        reason = str(result.get("end_reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    if counts:
        return counts

    for key in ("end_reason",):
        reason = report.get(key)
        if reason:
            games = _int(report.get("games")) or 1
            return {str(reason): games}
    matchups = report.get("matchups")
    if isinstance(matchups, list):
        for matchup in matchups:
            if not isinstance(matchup, dict):
                continue
            nested = matchup.get("report")
            if isinstance(nested, dict):
                for nested_reason, count in end_reason_stats_from_report(
                    nested, report_path=report_path
                ).items():
                    counts[nested_reason] = counts.get(nested_reason, 0) + count
    return counts


def _coerce_retry_stuck_log_entry(entry: object) -> dict[str, Any] | None:
    if isinstance(entry, dict):
        return entry if entry.get("event") == "retry_stuck_end" else None
    if not isinstance(entry, str):
        return None
    text = entry.strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("event") == "retry_stuck_end" else None


def find_retry_stuck_end_event(engine_logs: Iterable[object]) -> dict[str, Any] | None:
    """Return the last ``retry_stuck_end`` payload from persisted gateway engine logs."""

    for entry in reversed(list(engine_logs)):
        event = _coerce_retry_stuck_log_entry(entry)
        if event is not None:
            return event
    return None


def gateway_health_from_report(
    report: dict[str, Any],
    *,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Summarize where ``retry_stuck`` games stalled using ``retry_stuck_end`` engine logs."""

    prompt_counts: Counter[str] = Counter()
    tried_action_counts: Counter[str] = Counter()
    message_type_counts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    stuck_games = 0
    games_with_diagnostics = 0

    for raw_path in collect_game_log_paths(report):
        path = resolve_game_log_path(raw_path, report_path=report_path)
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = dict(payload.get("result", {}))
        if str(result.get("end_reason") or "") != "retry_stuck":
            continue
        stuck_games += 1
        stuck_event = find_retry_stuck_end_event(payload.get("engine_logs", ()))
        if stuck_event is None:
            continue
        games_with_diagnostics += 1
        prompt_name = str(
            stuck_event.get("last_prompt_name")
            or stuck_event.get("last_prompt_type")
            or "unknown",
        )
        prompt_counts[prompt_name] += 1
        for action_id in stuck_event.get("tried_action_ids", ()):
            tried_action_counts[str(action_id)] += 1
        message_types = stuck_event.get("message_types")
        if isinstance(message_types, list) and message_types:
            message_type_counts[", ".join(str(item) for item in message_types)] += 1
        else:
            message_type_counts["unknown"] += 1
        if len(examples) < 3:
            examples.append(
                {
                    "game_log": str(path),
                    "prompt": prompt_name,
                    "tried_action_ids": list(stuck_event.get("tried_action_ids", ())),
                    "message_types": list(message_types or ()),
                    "decisions": stuck_event.get("decisions"),
                    "duel_turn": stuck_event.get("duel_turn"),
                }
            )

    return {
        "stuck_games": stuck_games,
        "games_with_diagnostics": games_with_diagnostics,
        "top_prompts": prompt_counts.most_common(6),
        "top_tried_actions": tried_action_counts.most_common(10),
        "top_message_types": message_type_counts.most_common(4),
        "examples": examples,
    }


def format_gateway_health_lines(health: dict[str, Any]) -> list[str]:
    """Render gateway stall diagnostics for the English learning report."""

    stuck_games = int(health.get("stuck_games") or 0)
    if stuck_games <= 0:
        return []

    lines = ["", "Gateway health (retry_stuck):"]
    lines.append(f"- Simulation-fault games: {stuck_games}")
    diagnosed = int(health.get("games_with_diagnostics") or 0)
    if diagnosed < stuck_games:
        lines.append(
            f"- Games with `retry_stuck_end` logs: {diagnosed} "
            f"({stuck_games - diagnosed} missing engine diagnostics — restart gateway after updating gateway.mjs)."
        )

    top_prompts = list(health.get("top_prompts") or ())
    if top_prompts:
        parts = ", ".join(f"{name} ({count})" for name, count in top_prompts)
        lines.append(f"- Last prompt at stall: {parts}")

    top_actions = list(health.get("top_tried_actions") or ())
    if top_actions:
        parts = ", ".join(f"{action_id} ({count})" for action_id, count in top_actions[:8])
        lines.append(f"- Exhausted responses before abort: {parts}")

    top_messages = list(health.get("top_message_types") or ())
    if top_messages:
        parts = ", ".join(f"{pattern} ({count})" for pattern, count in top_messages)
        lines.append(f"- Engine message batch at stall: {parts}")

    examples = list(health.get("examples") or ())
    if examples:
        sample = examples[0]
        tried = ", ".join(str(item) for item in sample.get("tried_action_ids", ()))
        lines.append(
            f"- Example stall (turn {sample.get('duel_turn')}, decision {sample.get('decisions')}): "
            f"`{sample.get('prompt')}` after trying {tried or 'no logged responses'}."
        )

    if top_prompts:
        lead_prompt = str(top_prompts[0][0])
        if lead_prompt == "select_idlecmd":
            lines.append(
                "- Likely fix: idlecmd retries (set/activate/pos-change/zone placement). "
                "Ensure dashboard restarted with latest `gateways/edopro-ocgcore/gateway.mjs`."
            )
        elif lead_prompt == "select_battlecmd":
            lines.append(
                "- Likely fix: battlecmd retries (illegal direct attack or phase skip encoding). "
                "Restart gateway; prefer `to-end-phase` when attacks keep RETRY."
            )
        elif lead_prompt == "announce_card":
            lines.append(
                "- Likely fix: `announce_card` opcode matching (gateway was only sending card code 0). "
                "Restart dashboard with latest `gateways/edopro-ocgcore/gateway.mjs`."
            )
        elif lead_prompt in {"select_place", "select_card", "select_unselect_card", "select_chain"}:
            lines.append(
                f"- Likely fix: `{lead_prompt}` response encoding or stale prompt context on RETRY-only batches."
            )

    return lines


def tempo_stats_from_report(
    report: dict[str, Any],
    *,
    report_path: Path | None = None,
) -> dict[str, float]:
    """Summarize duel length and decision density from persisted game logs."""

    duel_turns: list[int] = []
    decision_counts: list[int] = []
    for raw_path in collect_game_log_paths(report):
        path = resolve_game_log_path(raw_path, report_path=report_path)
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = dict(payload.get("result", {}))
        traces = payload.get("traces")
        turns = _int(result.get("turns"))
        if isinstance(traces, list) and traces:
            trace_turns = [
                _int(trace.get("duel_turn", trace.get("turn", 0)))
                for trace in traces
                if isinstance(trace, dict)
            ]
            if trace_turns:
                turns = max(turns, max(trace_turns))
        if turns > 0:
            duel_turns.append(turns)
        if isinstance(traces, list) and traces:
            decision_counts.append(len(traces))

    if not duel_turns and not decision_counts:
        return {}

    stats: dict[str, float] = {}
    if duel_turns:
        stats["avg_duel_turns"] = sum(duel_turns) / len(duel_turns)
        stats["max_duel_turns"] = float(max(duel_turns))
    if decision_counts:
        stats["avg_decisions_per_game"] = sum(decision_counts) / len(decision_counts)
        stats["max_decisions_per_game"] = float(max(decision_counts))
    if duel_turns and decision_counts and len(duel_turns) == len(decision_counts):
        per_turn = [
            decisions / max(turns, 1)
            for decisions, turns in zip(decision_counts, duel_turns, strict=True)
        ]
        stats["avg_decisions_per_duel_turn"] = sum(per_turn) / len(per_turn)
    return stats


def _int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
