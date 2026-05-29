"""Track protagonist (Yugi / bot-01) improvement across bracket cycles."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def record_protagonist_progress(
    output_dir: Path,
    *,
    year: int,
    ethan_bot_id: str,
    season: dict[str, Any],
    policy_path: Path | None = None,
) -> dict[str, Any]:
    """Append one season snapshot to ``protagonist-progress.json``."""

    standings = season.get("standings", [])
    row = next(
        (item for item in standings if isinstance(item, dict) and str(item.get("bot_id")) == ethan_bot_id),
        None,
    )
    if not isinstance(row, dict):
        raise ValueError(f"missing standings row for protagonist {ethan_bot_id} in season {year}")

    policy_weights: dict[str, float] = {}
    policy_observations = 0
    if policy_path is not None and policy_path.is_file():
        from ygotrainingbot.policy_runtime import raw_tag_weights, read_policy_file

        policy_weights = raw_tag_weights(policy_path)
        policy_observations = int(read_policy_file(policy_path).get("observations", 0))

    snapshot = {
        "year": year,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "series_wins": int(row.get("series_wins", 0)),
        "series_losses": int(row.get("series_losses", 0)),
        "series_ties": int(row.get("series_ties", 0)),
        "series_win_rate": float(row.get("series_win_rate", 0.0)),
        "game_wins": int(row.get("game_wins", 0)),
        "game_losses": int(row.get("game_losses", 0)),
        "game_decisive_win_rate": float(row.get("game_decisive_win_rate", 0.0)),
        "policy_observations": policy_observations,
        "policy_tag_count": len(policy_weights),
        "policy_top_tags": sorted(
            policy_weights.items(),
            key=lambda item: abs(item[1]),
            reverse=True,
        )[:8],
    }

    progress_path = output_dir / "protagonist-progress.json"
    history: list[dict[str, Any]] = []
    if progress_path.is_file():
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
        raw_history = payload.get("history")
        if isinstance(raw_history, list):
            history = [item for item in raw_history if isinstance(item, dict)]

    history = [item for item in history if int(item.get("year", -1)) != year]
    history.append(snapshot)
    history.sort(key=lambda item: int(item.get("year", 0)))

    deltas: dict[str, float] = {}
    if len(history) >= 2:
        prev = history[-2]
        deltas = {
            "series_win_rate": snapshot["series_win_rate"] - float(prev.get("series_win_rate", 0.0)),
            "game_decisive_win_rate": snapshot["game_decisive_win_rate"]
            - float(prev.get("game_decisive_win_rate", 0.0)),
            "series_wins": snapshot["series_wins"] - int(prev.get("series_wins", 0)),
            "game_wins": snapshot["game_wins"] - int(prev.get("game_wins", 0)),
        }

    document = {
        "protagonist_bot_id": ethan_bot_id,
        "updated_at": snapshot["recorded_at"],
        "latest": snapshot,
        "delta_vs_previous": deltas,
        "history": history,
    }
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def _protagonist_snapshot_from_season(
    *,
    cycle: int,
    year: int,
    ethan_bot_id: str,
    season: dict[str, Any],
    policy_path: Path | None,
    status: str = "ok",
    error: str | None = None,
) -> dict[str, Any]:
    standings = season.get("standings", [])
    row = next(
        (item for item in standings if isinstance(item, dict) and str(item.get("bot_id")) == ethan_bot_id),
        None,
    )
    if not isinstance(row, dict):
        raise ValueError(f"missing standings row for protagonist {ethan_bot_id}")

    policy_weights: dict[str, float] = {}
    policy_observations = 0
    if policy_path is not None and policy_path.is_file():
        from ygotrainingbot.policy_runtime import raw_tag_weights, read_policy_file

        policy_weights = raw_tag_weights(policy_path)
        policy_observations = int(read_policy_file(policy_path).get("observations", 0))

    learning = season.get("learning", {})
    bot_learning = {}
    if isinstance(learning, dict):
        raw = learning.get("bots", {})
        if isinstance(raw, dict):
            bot_learning = dict(raw.get(ethan_bot_id, {}))

    return {
        "cycle": cycle,
        "year": year,
        "status": status,
        "error": error,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "series_wins": int(row.get("series_wins", 0)),
        "series_losses": int(row.get("series_losses", 0)),
        "series_ties": int(row.get("series_ties", 0)),
        "series_win_rate": float(row.get("series_win_rate", 0.0)),
        "game_wins": int(row.get("game_wins", 0)),
        "game_losses": int(row.get("game_losses", 0)),
        "game_decisive_win_rate": float(row.get("game_decisive_win_rate", 0.0)),
        "policy_observations": policy_observations,
        "policy_tag_count": len(policy_weights),
        "policy_top_tags": sorted(
            policy_weights.items(),
            key=lambda item: abs(item[1]),
            reverse=True,
        )[:8],
        "learning": bot_learning,
    }


def record_training_loop_cycle(
    output_dir: Path,
    *,
    cycle: int,
    year: int,
    ethan_bot_id: str,
    season: dict[str, Any] | None,
    policy_path: Path | None = None,
    status: str = "ok",
    error: str | None = None,
) -> dict[str, Any]:
    """Append one training-loop cycle to ``training-loop-progress.json``."""

    progress_path = output_dir / "training-loop-progress.json"
    document: dict[str, Any] = {
        "protagonist_bot_id": ethan_bot_id,
        "format_year": year,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cycles": [],
        "errors": [],
    }
    if progress_path.is_file():
        loaded = json.loads(progress_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            document.update(loaded)

    cycles = [item for item in document.get("cycles", []) if isinstance(item, dict)]
    cycles = [item for item in cycles if int(item.get("cycle", -1)) != cycle]

    if season is not None and status == "ok":
        snapshot = _protagonist_snapshot_from_season(
            cycle=cycle,
            year=year,
            ethan_bot_id=ethan_bot_id,
            season=season,
            policy_path=policy_path,
            status=status,
            error=error,
        )
        cycles.append(snapshot)
    else:
        cycles.append(
            {
                "cycle": cycle,
                "year": year,
                "status": status,
                "error": error,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )

    cycles.sort(key=lambda item: int(item.get("cycle", 0)))
    document["cycles"] = cycles
    document["completed_cycles"] = len([item for item in cycles if item.get("status") == "ok"])
    document["failed_cycles"] = len([item for item in cycles if item.get("status") != "ok"])

    ok_cycles = [item for item in cycles if item.get("status") == "ok"]
    if ok_cycles:
        latest = ok_cycles[-1]
        document["latest"] = latest
        if len(ok_cycles) >= 2:
            prev = ok_cycles[-2]
            document["delta_vs_previous"] = {
                "series_win_rate": float(latest.get("series_win_rate", 0.0))
                - float(prev.get("series_win_rate", 0.0)),
                "game_decisive_win_rate": float(latest.get("game_decisive_win_rate", 0.0))
                - float(prev.get("game_decisive_win_rate", 0.0)),
                "series_wins": int(latest.get("series_wins", 0)) - int(prev.get("series_wins", 0)),
                "game_wins": int(latest.get("game_wins", 0)) - int(prev.get("game_wins", 0)),
            }

    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def should_stop_on_regression(
    progress: dict[str, Any],
    *,
    tolerance: float = 0.05,
) -> tuple[bool, str | None]:
    """Return True when the latest cycle regressed beyond tolerance vs the previous ok cycle."""

    cycles = [item for item in progress.get("cycles", []) if isinstance(item, dict) and item.get("status") == "ok"]
    if len(cycles) < 2:
        return False, None
    latest = cycles[-1]
    previous = cycles[-2]
    delta = float(latest.get("series_win_rate", 0.0)) - float(previous.get("series_win_rate", 0.0))
    if delta < -abs(tolerance):
        return True, (
            f"series win rate dropped {abs(delta):.3f} "
            f"(from {float(previous.get('series_win_rate', 0.0)):.3f} "
            f"to {float(latest.get('series_win_rate', 0.0)):.3f})"
        )
    return False, None


def render_training_loop_summary(progress: dict[str, Any]) -> str:
    """Plain-English summary for overnight loop runs."""

    cycles = [item for item in progress.get("cycles", []) if isinstance(item, dict)]
    ok_cycles = [item for item in cycles if item.get("status") == "ok"]
    failed = [item for item in cycles if item.get("status") != "ok"]
    lines = [
        "Yu-Gi-Oh training loop report",
        "",
        f"Completed cycles: {len(ok_cycles)}",
        f"Failed cycles: {len(failed)}",
    ]
    if ok_cycles:
        first = ok_cycles[0]
        last = ok_cycles[-1]
        lines.extend(
            [
                "",
                "Protagonist (Yugi) progression:",
                f"- Cycle 1 series win rate: {float(first.get('series_win_rate', 0.0)):.3f}",
                f"- Latest cycle series win rate: {float(last.get('series_win_rate', 0.0)):.3f}",
                f"- Latest cycle decisive game win rate: {float(last.get('game_decisive_win_rate', 0.0)):.3f}",
                f"- Policy observations: {int(last.get('policy_observations', 0))}",
            ]
        )
        delta = progress.get("delta_vs_previous")
        if isinstance(delta, dict) and delta:
            lines.append("")
            lines.append("Change since previous completed cycle:")
            for key, value in delta.items():
                lines.append(f"- {key}: {value:+.3f}" if isinstance(value, float) else f"- {key}: {value:+}")
    if failed:
        lines.extend(["", "Failures:"])
        for item in failed[-10:]:
            lines.append(f"- Cycle {item.get('cycle')}: {item.get('error', item.get('status'))}")
    lines.append("")
    return "\n".join(lines)
