"""Detect Lua/script runtime problems in gateway logs."""

from __future__ import annotations

from typing import Any, Iterable


_SCRIPT_ERROR_MARKERS = (
    "GetID",
    "CallCardFunction",
    "attempt to call a nil value",
    "attempt to call an error function",
)


def is_script_runtime_error_message(message: object) -> bool:
    text = str(message)
    return any(marker in text for marker in _SCRIPT_ERROR_MARKERS)


def count_script_runtime_errors(
    gateway_logs: Iterable[object] | None,
    *,
    script_stats: dict[str, Any] | None = None,
) -> int:
    """Count script runtime failures from engine logs and gateway script_stats."""

    if script_stats is not None:
        reported = int(script_stats.get("runtime_errors", 0) or 0)
        if reported > 0:
            return reported

    count = 0
    for entry in gateway_logs or ():
        if isinstance(entry, dict):
            if entry.get("event") == "script_runtime_error":
                count += 1
                continue
            message = entry.get("message", "")
        else:
            message = entry
        if is_script_runtime_error_message(message):
            count += 1
    return count


def script_health_summary(
    gateway_logs: Iterable[object] | None,
    *,
    script_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors = count_script_runtime_errors(gateway_logs, script_stats=script_stats)
    preludes = []
    if script_stats:
        raw = script_stats.get("prelude_loaded")
        if isinstance(raw, list):
            preludes = [str(item) for item in raw]
    return {
        "runtime_errors": errors,
        "clean": errors == 0,
        "prelude_loaded": preludes,
        "script_stats": dict(script_stats or {}),
    }
