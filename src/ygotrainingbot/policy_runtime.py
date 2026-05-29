"""Policy file I/O, learned-weight scaling, and promotion helpers."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

DEFAULT_LEARNED_WEIGHT_SCALE = 25.0


def read_policy_file(policy_path: Path) -> dict[str, Any]:
    if not policy_path.is_file():
        return {"tag_weights": {}, "observations": 0, "learned_weight_scale": DEFAULT_LEARNED_WEIGHT_SCALE}
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"policy file must be a JSON object: {policy_path}")
    return payload


def raw_tag_weights(policy_path: Path | None) -> dict[str, float]:
    if policy_path is None or not policy_path.exists():
        return {}
    payload = read_policy_file(policy_path)
    return {
        str(tag): float(weight)
        for tag, weight in dict(payload.get("tag_weights", {})).items()
    }


def learned_weight_scale(policy_path: Path | None) -> float:
    if policy_path is None or not policy_path.exists():
        return DEFAULT_LEARNED_WEIGHT_SCALE
    payload = read_policy_file(policy_path)
    return float(payload.get("learned_weight_scale", DEFAULT_LEARNED_WEIGHT_SCALE))


def scaled_tag_weights_for_play(
    policy_path: Path | None,
    *,
    scale: float | None = None,
) -> dict[str, float] | None:
    if policy_path is None or not policy_path.exists():
        return None
    raw = raw_tag_weights(policy_path)
    if not raw:
        return {}
    multiplier = DEFAULT_LEARNED_WEIGHT_SCALE if scale is None else scale
    if scale is None:
        multiplier = learned_weight_scale(policy_path)
    return {tag: weight * multiplier for tag, weight in raw.items()}


def write_policy_file(
    policy_path: Path,
    tag_weights: dict[str, float],
    *,
    observations: int | None = None,
    learned_weight_scale_value: float = DEFAULT_LEARNED_WEIGHT_SCALE,
    parent_observations: int | None = None,
) -> None:
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    previous = read_policy_file(policy_path) if policy_path.exists() else {}
    payload: dict[str, Any] = {
        "tag_weights": {str(tag): float(weight) for tag, weight in sorted(tag_weights.items())},
        "observations": observations if observations is not None else int(previous.get("observations", 0)),
        "learned_weight_scale": learned_weight_scale_value,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if parent_observations is not None:
        payload["parent_observations"] = parent_observations
    elif "parent_observations" in previous:
        payload["parent_observations"] = previous["parent_observations"]
    if "version" in previous:
        payload["version"] = int(previous["version"]) + 1
    else:
        payload["version"] = 1
    policy_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def backup_policy(policy_path: Path) -> Path:
    backup_path = policy_path.with_suffix(".prev.json")
    if policy_path.exists():
        shutil.copy2(policy_path, backup_path)
    return backup_path


def restore_policy(policy_path: Path, backup_path: Path) -> bool:
    if not backup_path.is_file():
        return False
    shutil.copy2(backup_path, policy_path)
    return True


def policy_change_magnitude(before: dict[str, float], after: dict[str, float]) -> float:
    keys = set(before) | set(after)
    return sum(abs(float(after.get(key, 0.0)) - float(before.get(key, 0.0))) for key in keys)


def reset_cycle_observations(policy_path: Path) -> int:
    """Reset running observations to the parent baseline before a new learning cycle."""

    payload = read_policy_file(policy_path)
    baseline = int(payload.get("parent_observations", payload.get("observations", 0)))
    write_policy_file(
        policy_path,
        {str(tag): float(weight) for tag, weight in dict(payload.get("tag_weights", {})).items()},
        observations=baseline,
        learned_weight_scale_value=float(payload.get("learned_weight_scale", DEFAULT_LEARNED_WEIGHT_SCALE)),
    )
    return baseline


def resolve_bot_agent(bot_report: dict[str, Any]) -> str:
    """Return the bot id this report describes, when available."""

    bot_agent = str(bot_report.get("bot_agent", "")).strip()
    if bot_agent:
        return bot_agent
    wins = {str(k): int(v) for k, v in dict(bot_report.get("wins_by_agent", {})).items()}
    if len(wins) == 1:
        return next(iter(wins))
    format_name = str(bot_report.get("format", ""))
    if ":" in format_name:
        return format_name.rsplit(":", 1)[-1]
    return ""


def should_accept_policy_update(
    bot_report: dict[str, Any],
    before: dict[str, float],
    after: dict[str, float],
) -> bool:
    """Cheap promotion gate: accept learning unless the update clearly regressed."""

    if not after:
        return False
    if not before:
        return True

    bot_agent = resolve_bot_agent(bot_report)
    wins = {str(k): int(v) for k, v in dict(bot_report.get("wins_by_agent", {})).items()}
    bot_wins = wins.get(bot_agent, 0)
    games = max(1, int(bot_report.get("games", 0)))
    decisive_rate = bot_wins / games

    magnitude = policy_change_magnitude(before, after)
    if magnitude < 0.01:
        return True

    # Bot played at least some winning games this season — trust the update.
    if bot_wins > 0 and decisive_rate >= 0.35:
        return True

    # Small nudges are always fine.
    if magnitude <= 8.0:
        return True

    # Large swings without wins are suspicious — reject.
    if bot_wins == 0 and magnitude > 15.0:
        return False

    return True
