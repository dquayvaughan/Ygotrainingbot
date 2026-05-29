"""Persist full duel traces and engine logs for training."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ygotrainingbot.models import DuelTrace, MatchResult


def trace_to_dict(trace: DuelTrace) -> dict[str, Any]:
    return {
        "agent": trace.agent_name,
        "note": trace.note,
        "turn": trace.state.turn,
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
    return {
        "turn": trace.get("turn"),
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

    def annotate(sample: dict[str, Any]) -> dict[str, Any]:
        agent = str(sample.get("agent") or "")
        if winner and agent:
            sample["game_won"] = agent == winner
        if goes_first and agent:
            sample["bot_goes_first"] = goes_first == agent
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
