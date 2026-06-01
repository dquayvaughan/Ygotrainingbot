"""Aggregate per-bot training stats for the dashboard."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ygotrainingbot.duel_logs import tempo_stats_from_report
from ygotrainingbot.human_duels import load_catalog
from ygotrainingbot.policy_runtime import read_policy_file


@dataclass(frozen=True, slots=True)
class BotStatsPaths:
    """Filesystem locations used to rebuild bot stats."""

    repo_root: Path
    jobs_dir: Path
    bots_dir: Path
    human_catalog_dir: Path
    roster_path: Path
    stats_path: Path


def _int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _resolve_roster_path(repo_root: Path, roster_path: Path) -> Path:
    if roster_path.is_absolute():
        return roster_path.resolve()
    return (repo_root / roster_path).resolve()


def _load_roster_bots(repo_root: Path, roster_path: Path) -> list[dict[str, Any]]:
    path = _resolve_roster_path(repo_root, roster_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    bots = payload.get("bots", [])
    if not isinstance(bots, list):
        return []
    return [
        {
            "bot_id": str(bot.get("bot_id", "")),
            "name": str(bot.get("name", bot.get("bot_id", ""))),
            "policy": str(bot.get("policy", "heuristic")),
        }
        for bot in bots
        if isinstance(bot, dict) and bot.get("bot_id")
    ]


def _agent_aliases(bot: dict[str, Any]) -> set[str]:
    bot_id = str(bot["bot_id"])
    name = str(bot["name"])
    return {bot_id, name, bot_id.lower(), name.lower()}


def _match_bot_id(agent: str | None, roster: list[dict[str, Any]]) -> str | None:
    if not agent:
        return None
    key = agent.strip().lower()
    for bot in roster:
        if key in _agent_aliases(bot):
            return str(bot["bot_id"])
    return None


def _empty_bot_row(bot: dict[str, Any]) -> dict[str, Any]:
    return {
        "bot_id": bot["bot_id"],
        "name": bot["name"],
        "policy": bot["policy"],
        "training_duels": 0,
        "human_duels": 0,
        "training_sessions": 0,
        "policy_updates": 0,
        "policy_observations": 0,
        "learned_tags": 0,
        "avg_duel_turns": None,
        "avg_decisions_per_game": None,
        "avg_decisions_per_duel_turn": None,
        "total_decisions": 0,
        "last_trained_at": None,
    }


def _merge_tempo(row: dict[str, Any], tempo: dict[str, float], *, games: int, decisions: int) -> None:
    if games > 0:
        row["training_duels"] = int(row["training_duels"]) + games
    if decisions > 0:
        row["total_decisions"] = int(row["total_decisions"]) + decisions

    for key in ("avg_duel_turns", "avg_decisions_per_game", "avg_decisions_per_duel_turn"):
        value = tempo.get(key)
        if value is None:
            continue
        prior_avg = row.get(key)
        prior_count = int(row.get(f"_{key}_samples", 0))
        new_count = prior_count + (1 if games > 0 else 0)
        if prior_avg is None or prior_count == 0:
            row[key] = round(value, 2)
        else:
            row[key] = round((float(prior_avg) * prior_count + value) / new_count, 2)
        row[f"_{key}_samples"] = new_count


def _accumulate_report(
    rows: dict[str, dict[str, Any]],
    report: dict[str, Any],
    *,
    bot_id: str | None,
    report_path: Path | None = None,
) -> None:
    games = _int(report.get("total_games")) or _int(report.get("games"))
    decisions = _int(report.get("total_traced_decisions")) or _int(report.get("traced_decisions"))
    tempo = tempo_stats_from_report(report, report_path=report_path)

    targets: list[str] = []
    if bot_id and bot_id in rows:
        targets.append(bot_id)

    bot_reports = report.get("bot_training_reports")
    if isinstance(bot_reports, dict):
        for nested_id, nested in bot_reports.items():
            if not isinstance(nested, dict):
                continue
            bid = str(nested_id)
            if bid not in rows:
                continue
            nested_games = _int(nested.get("total_games")) or _int(nested.get("games"))
            nested_decisions = _int(nested.get("total_traced_decisions")) or _int(nested.get("traced_decisions"))
            nested_tempo = tempo_stats_from_report(nested, report_path=report_path)
            _merge_tempo(rows[bid], nested_tempo, games=nested_games, decisions=nested_decisions)

    for target in targets:
        _merge_tempo(rows[target], tempo, games=games, decisions=decisions)


def _scan_completed_jobs(paths: BotStatsPaths, rows: dict[str, dict[str, Any]]) -> int:
    sessions = 0
    if not paths.jobs_dir.is_dir():
        return sessions

    for meta_path in sorted(paths.jobs_dir.glob("*/meta.json")):
        try:
            job = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if str(job.get("status")) != "completed":
            continue

        job_id = str(job.get("job_id") or meta_path.parent.name)
        report_path = meta_path.parent / "report.json"
        report: dict[str, Any] | None = None
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                report = None

        bot_id = job.get("bot_id")
        if isinstance(bot_id, str) and bot_id in rows:
            rows[bot_id]["training_sessions"] = int(rows[bot_id]["training_sessions"]) + 1
            finished = job.get("finished_at")
            if finished:
                rows[bot_id]["last_trained_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(float(finished)),
                )
            sessions += 1

        if report is not None:
            _accumulate_report(
                rows,
                report,
                bot_id=str(bot_id) if isinstance(bot_id, str) else None,
                report_path=report_path,
            )

        policy_path = meta_path.parent / "learned-policy.json"
        if isinstance(bot_id, str) and bot_id in rows and policy_path.is_file():
            payload = read_policy_file(policy_path)
            rows[bot_id]["policy_updates"] = max(
                int(rows[bot_id]["policy_updates"]),
                _int(payload.get("version")),
            )
            rows[bot_id]["policy_observations"] = max(
                int(rows[bot_id]["policy_observations"]),
                _int(payload.get("observations")),
            )
            rows[bot_id]["learned_tags"] = max(
                int(rows[bot_id]["learned_tags"]),
                len(dict(payload.get("tag_weights", {}))),
            )

    return sessions


def _scan_bot_policies(paths: BotStatsPaths, rows: dict[str, dict[str, Any]]) -> None:
    if not paths.bots_dir.is_dir():
        return
    for bot_id, row in rows.items():
        policy_path = paths.bots_dir / bot_id / "policy.json"
        if not policy_path.is_file():
            continue
        payload = read_policy_file(policy_path)
        row["policy_updates"] = max(int(row["policy_updates"]), _int(payload.get("version")))
        row["policy_observations"] = max(int(row["policy_observations"]), _int(payload.get("observations")))
        row["learned_tags"] = max(int(row["learned_tags"]), len(dict(payload.get("tag_weights", {}))))
        updated = payload.get("updated_at")
        if updated and (row["last_trained_at"] is None or str(updated) > str(row["last_trained_at"])):
            row["last_trained_at"] = str(updated)


def _scan_human_catalog(
    paths: BotStatsPaths,
    rows: dict[str, dict[str, Any]],
    roster: list[dict[str, Any]],
) -> None:
    catalog_dir = paths.human_catalog_dir
    if not catalog_dir.is_dir():
        return
    for entry in load_catalog(catalog_dir):
        bot_id = _match_bot_id(entry.study_agent, roster)
        if not bot_id or bot_id not in rows:
            continue
        rows[bot_id]["human_duels"] = int(rows[bot_id]["human_duels"]) + 1
        rows[bot_id]["total_decisions"] = int(rows[bot_id]["total_decisions"]) + int(entry.decision_count)


def rebuild_bot_stats(paths: BotStatsPaths) -> dict[str, Any]:
    """Scan jobs, policies, and human catalog; return a dashboard-ready snapshot."""

    roster = _load_roster_bots(paths.repo_root, paths.roster_path)
    rows = {str(bot["bot_id"]): _empty_bot_row(bot) for bot in roster}

    job_sessions = _scan_completed_jobs(paths, rows)
    _scan_bot_policies(paths, rows)
    _scan_human_catalog(paths, rows, roster)

    for row in rows.values():
        for key in list(row.keys()):
            if key.startswith("_"):
                del row[key]

    bots = sorted(rows.values(), key=lambda item: str(item["bot_id"]))
    totals = {
        "training_duels": sum(int(bot["training_duels"]) for bot in bots),
        "human_duels": sum(int(bot["human_duels"]) for bot in bots),
        "training_sessions": sum(int(bot["training_sessions"]) for bot in bots),
        "total_decisions": sum(int(bot["total_decisions"]) for bot in bots),
        "completed_jobs": job_sessions,
    }
    return {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bots": bots,
        "totals": totals,
    }


def _empty_bot_stats() -> dict[str, Any]:
    return {"updated_at": None, "bots": [], "totals": {}}


_stats_write_lock = threading.Lock()


def write_bot_stats(paths: BotStatsPaths) -> Path:
    snapshot = rebuild_bot_stats(paths)
    paths.stats_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    legacy_tmp = paths.stats_path.with_suffix(paths.stats_path.suffix + ".tmp")

    with _stats_write_lock:
        fd, tmp_name = tempfile.mkstemp(
            suffix=".json",
            prefix="bot-training-stats-",
            dir=paths.stats_path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(tmp_name, paths.stats_path)
            except OSError:
                # OneDrive / AV on Windows often blocks rename-over-existing.
                paths.stats_path.write_text(payload, encoding="utf-8")
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass
        try:
            legacy_tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return paths.stats_path


def load_bot_stats(stats_path: Path) -> dict[str, Any]:
    if not stats_path.is_file():
        return _empty_bot_stats()
    raw = stats_path.read_text(encoding="utf-8").strip()
    if not raw:
        return _empty_bot_stats()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _empty_bot_stats()
    if not isinstance(data, dict):
        return _empty_bot_stats()
    return data
