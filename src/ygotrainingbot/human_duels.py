"""Import and learn from duels played by real people.

Supported inputs (JSON):

1. **Full game log** — same shape as simulator ``game-*.json`` files (``meta``, ``result``,
   ``traces``). Set ``meta.source`` to ``"human"`` (added automatically on import).

2. **Decisions-only log** — lighter files for hand-labeled or converted replays with a
   top-level ``decisions`` list instead of full ``traces``.

EDOPro ``.yrp`` / ``.yrpX`` files must be converted first — use
``python -m ygotrainingbot.cli convert-edopro-replay path/to/replay.yrpX``
(``.yrpX`` recommended; legacy ``.yrp`` is not supported yet).
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ygotrainingbot.duel_logs import (
    _decision_dict_to_trace,
    collect_game_log_paths,
    load_decision_samples_for_learning,
    samples_from_game_log_payload,
)

HUMAN_SOURCE = "human"
MANIFEST_NAME = "manifest.json"
DEFAULT_CATALOG_DIR = Path("data/human-duels")


@dataclass(frozen=True, slots=True)
class HumanDuelEntry:
    """One imported human duel in the catalog."""

    duel_id: str
    path: str
    format: str
    study_agent: str | None
    player_a: str | None
    player_b: str | None
    winner: str | None
    decision_count: int
    imported_at: str


@dataclass
class HumanDuelImportResult:
    catalog_dir: Path
    imported: list[HumanDuelEntry] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def import_human_duels(
    input_dir: Path,
    *,
    catalog_dir: Path = DEFAULT_CATALOG_DIR,
    copy_files: bool = True,
    glob_pattern: str = "**/*.json",
) -> HumanDuelImportResult:
    """Scan *input_dir*, validate duel JSON, optionally copy into *catalog_dir*/duels/."""

    result = HumanDuelImportResult(catalog_dir=catalog_dir.resolve())
    if not input_dir.is_dir():
        result.errors.append({"path": str(input_dir), "error": "input directory does not exist"})
        return result

    seen_ids: set[str] = set()
    for path in sorted(input_dir.glob(glob_pattern)):
        if not path.is_file():
            continue
        if path.name == MANIFEST_NAME:
            continue
        if path.parent.name == "examples":
            continue
        _import_payload_from_path(
            path,
            result=result,
            catalog_dir=catalog_dir,
            copy_files=copy_files,
            seen_ids=seen_ids,
        )

    _merge_imported_entries(catalog_dir, result.imported)
    return result


def import_human_duel_files(
    files: list[tuple[str, bytes | str]],
    *,
    catalog_dir: Path = DEFAULT_CATALOG_DIR,
) -> HumanDuelImportResult:
    """Import one or more in-memory JSON duel logs (for uploads and APIs)."""

    result = HumanDuelImportResult(catalog_dir=catalog_dir.resolve())
    seen_ids = {entry.duel_id for entry in _load_manifest_entries(catalog_dir)}

    for filename, raw in files:
        label = Path(filename).name or "upload.json"
        if not label.lower().endswith(".json"):
            label = f"{label}.json"

        if isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                result.errors.append({"path": label, "error": str(exc)})
                continue
        else:
            text = raw

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            result.errors.append({"path": label, "error": str(exc)})
            continue

        validation_errors = validate_human_duel(payload)
        if validation_errors:
            result.errors.append({"path": label, "error": "; ".join(validation_errors)})
            continue

        source_path = Path(label)
        normalized = normalize_human_duel_payload(payload, source_path=source_path)
        duel_id = _duel_id_from_path(source_path, seen_ids)
        seen_ids.add(duel_id)

        duels_dir = catalog_dir / "duels"
        duels_dir.mkdir(parents=True, exist_ok=True)
        dest = duels_dir / f"{duel_id}.json"
        dest.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result.imported.append(_entry_from_payload(duel_id, dest, normalized))

    _merge_imported_entries(catalog_dir, result.imported)
    return result


def catalog_summary(catalog_dir: Path) -> dict[str, Any]:
    """Return catalog stats for dashboards and APIs."""

    entries = load_catalog(catalog_dir)
    formats = sorted({entry.format for entry in entries if entry.format})
    study_agents = sorted({entry.study_agent for entry in entries if entry.study_agent})
    return {
        "catalog_dir": str(catalog_dir.resolve()),
        "duel_count": len(entries),
        "total_decisions": sum(entry.decision_count for entry in entries),
        "formats": formats,
        "study_agents": study_agents,
        "duels": [
            {
                "duel_id": entry.duel_id,
                "format": entry.format,
                "study_agent": entry.study_agent,
                "player_a": entry.player_a,
                "player_b": entry.player_b,
                "winner": entry.winner,
                "decision_count": entry.decision_count,
                "imported_at": entry.imported_at,
            }
            for entry in entries
        ],
    }


def _import_payload_from_path(
    path: Path,
    *,
    result: HumanDuelImportResult,
    catalog_dir: Path,
    copy_files: bool,
    seen_ids: set[str],
) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.errors.append({"path": str(path), "error": str(exc)})
        return

    validation_errors = validate_human_duel(payload)
    if validation_errors:
        result.errors.append({"path": str(path), "error": "; ".join(validation_errors)})
        return

    normalized = normalize_human_duel_payload(payload, source_path=path)
    duel_id = _duel_id_from_path(path, seen_ids)
    seen_ids.add(duel_id)

    if copy_files:
        duels_dir = catalog_dir / "duels"
        duels_dir.mkdir(parents=True, exist_ok=True)
        dest = duels_dir / f"{duel_id}.json"
        dest.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        stored_path = dest
    else:
        stored_path = path.resolve()

    result.imported.append(_entry_from_payload(duel_id, stored_path, normalized))


def _merge_imported_entries(catalog_dir: Path, imported: list[HumanDuelEntry]) -> None:
    if not imported:
        return
    existing = _load_manifest_entries(catalog_dir)
    by_id = {item.duel_id: item for item in existing}
    for entry in imported:
        by_id[entry.duel_id] = entry
    _write_manifest(catalog_dir, list(by_id.values()))


def build_learning_report(
    catalog_dir: Path,
    *,
    study_agent: str | None = None,
    format_filter: str | None = None,
    bot_agent: str | None = None,
) -> dict[str, Any]:
    """Build a training report dict compatible with :func:`learning.learn_from_report`."""

    entries = load_catalog(catalog_dir)
    if format_filter:
        entries = [entry for entry in entries if entry.format == format_filter]
    if not entries:
        raise ValueError(f"No human duels found in catalog {catalog_dir}")

    all_samples: list[dict[str, Any]] = []
    game_log_paths: list[str] = []
    wins = Counter()
    tags: Counter = Counter()
    actions: Counter = Counter()
    total_games = 0

    for entry in entries:
        path = Path(entry.path)
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        agent = study_agent or entry.study_agent
        samples = samples_from_human_duel_payload(payload, study_agent=agent)
        if not samples:
            continue

        total_games += 1
        game_log_paths.append(str(path.resolve()))
        winner = entry.winner or _winner_from_payload(payload)
        if winner and agent and winner == agent:
            wins[str(agent)] += 1
        elif winner and agent:
            wins["opponent"] += 1

        for sample in samples:
            all_samples.append(sample)
            tags.update(str(tag) for tag in sample.get("selected_tags", ()))
            action = sample.get("selected_action")
            if action:
                actions[str(action)] += 1

    if not all_samples:
        total_decisions = sum(entry.decision_count for entry in entries)
        if total_decisions == 0:
            raise ValueError(
                "Imported replays have no decisions — re-upload .yrpX files or run "
                "convert-edopro-replay on your EDOPro replay/ folder"
            )
        raise ValueError(
            "No decision samples matched the requested study_agent / format filter"
        )

    format_name = format_filter or entries[0].format
    report: dict[str, Any] = {
        "format": f"human:{format_name}",
        "source": HUMAN_SOURCE,
        "total_games": total_games,
        "total_traced_decisions": len(all_samples),
        "games": total_games,
        "traced_decisions": len(all_samples),
        "draws": 0,
        "wins_by_agent": dict(wins),
        "tags": dict(tags),
        "action_counts": dict(actions),
        "decision_samples": all_samples,
        "game_log_paths": game_log_paths,
        "human_duel_count": len(game_log_paths),
    }
    effective_bot = bot_agent or study_agent or entries[0].study_agent
    if effective_bot:
        report["bot_agent"] = effective_bot
    return report


def write_learning_report(catalog_dir: Path, report: dict[str, Any], *, name: str = "human-learning-report.json") -> Path:
    path = catalog_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_catalog(catalog_dir: Path) -> list[HumanDuelEntry]:
    return _load_manifest_entries(catalog_dir)


def validate_human_duel(payload: dict[str, Any]) -> list[str]:
    """Return validation error messages (empty if valid)."""

    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload must be a JSON object"]

    meta = payload.get("meta")
    if meta is not None and not isinstance(meta, dict):
        errors.append("meta must be an object")

    traces = payload.get("traces")
    decisions = payload.get("decisions")
    if traces is None and decisions is None:
        errors.append("need either 'traces' (full log) or 'decisions' (lite log)")
    if traces is not None and not isinstance(traces, list):
        errors.append("traces must be a list")
    if decisions is not None and not isinstance(decisions, list):
        errors.append("decisions must be a list")
    if isinstance(traces, list) and not traces and isinstance(decisions, list) and not decisions:
        errors.append("traces and decisions cannot both be empty")

    if isinstance(decisions, list):
        for index, item in enumerate(decisions[:5]):
            if not isinstance(item, dict):
                errors.append(f"decisions[{index}] must be an object")
                break
            if not item.get("selected_action") and not item.get("selected_label"):
                errors.append(f"decisions[{index}] needs selected_action or selected_label")

    return errors


def normalize_human_duel_payload(payload: dict[str, Any], *, source_path: Path | None = None) -> dict[str, Any]:
    """Ensure human metadata and convert decisions-only files to trace-compatible shape."""

    normalized = json.loads(json.dumps(payload))
    meta = dict(normalized.get("meta") or {})
    meta["source"] = HUMAN_SOURCE
    if source_path is not None:
        meta.setdefault("imported_from", str(source_path.resolve()))
    if not meta.get("format"):
        meta["format"] = "unknown"
    normalized["meta"] = meta

    if normalized.get("traces"):
        return normalized

    decisions = normalized.get("decisions")
    if not isinstance(decisions, list):
        return normalized

    normalized["traces"] = [_decision_dict_to_trace(item) for item in decisions if isinstance(item, dict)]
    return normalized


def samples_from_human_duel_payload(
    payload: dict[str, Any],
    *,
    study_agent: str | None = None,
) -> list[dict[str, Any]]:
    """Extract learning samples from a normalized human duel payload."""

    normalized = normalize_human_duel_payload(payload, source_path=None)
    samples = samples_from_game_log_payload(normalized)
    if study_agent:
        samples = [sample for sample in samples if str(sample.get("agent")) == study_agent]
    return samples


def _entry_from_payload(duel_id: str, path: Path, payload: dict[str, Any]) -> HumanDuelEntry:
    meta = payload.get("meta") or {}
    result = payload.get("result") or {}
    study = meta.get("study_agent")
    samples = samples_from_human_duel_payload(payload, study_agent=str(study) if study else None)
    if not samples and payload.get("traces"):
        samples = samples_from_human_duel_payload(payload, study_agent=None)

    return HumanDuelEntry(
        duel_id=duel_id,
        path=str(path.resolve()),
        format=str(meta.get("format", "unknown")),
        study_agent=str(study) if study else None,
        player_a=_player_name(meta, "a"),
        player_b=_player_name(meta, "b"),
        winner=_winner_from_payload(payload),
        decision_count=len(samples),
        imported_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def _winner_from_payload(payload: dict[str, Any]) -> str | None:
    result = payload.get("result")
    if isinstance(result, dict) and result.get("winner"):
        return str(result["winner"])
    meta = payload.get("meta")
    if isinstance(meta, dict) and meta.get("winner"):
        return str(meta["winner"])
    return None


def _player_name(meta: dict[str, Any], side: str) -> str | None:
    for key in (f"player_{side}", f"player-{side}", f"player{side.upper()}"):
        if meta.get(key):
            return str(meta[key])
    return None


def _duel_id_from_path(path: Path, seen: set[str]) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", path.stem).strip("-").lower() or "duel"
    candidate = base
    counter = 2
    while candidate in seen:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _load_manifest_entries(catalog_dir: Path) -> list[HumanDuelEntry]:
    manifest_path = catalog_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return []
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    duels = payload.get("duels")
    if not isinstance(duels, list):
        return []
    entries: list[HumanDuelEntry] = []
    for item in duels:
        if not isinstance(item, dict):
            continue
        duel_id = item.get("duel_id")
        if not duel_id:
            continue
        entries.append(
            HumanDuelEntry(
                duel_id=str(duel_id),
                path=str(item["path"]),
                format=str(item.get("format", "unknown")),
                study_agent=item.get("study_agent"),
                player_a=item.get("player_a"),
                player_b=item.get("player_b"),
                winner=item.get("winner"),
                decision_count=int(item.get("decision_count", 0)),
                imported_at=str(item.get("imported_at", "")),
            )
        )
    return entries


def _write_manifest(catalog_dir: Path, entries: list[HumanDuelEntry]) -> None:
    catalog_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": HUMAN_SOURCE,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duel_count": len(entries),
        "duels": [
            {
                "duel_id": entry.duel_id,
                "path": entry.path,
                "format": entry.format,
                "study_agent": entry.study_agent,
                "player_a": entry.player_a,
                "player_b": entry.player_b,
                "winner": entry.winner,
                "decision_count": entry.decision_count,
                "imported_at": entry.imported_at,
            }
            for entry in sorted(entries, key=lambda item: item.duel_id)
        ],
    }
    (catalog_dir / MANIFEST_NAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# Re-export learning helpers that accept human reports built here.
def human_report_game_log_paths(report: dict[str, Any]) -> list[Path]:
    return collect_game_log_paths(report)


def human_report_samples(report: dict[str, Any], *, report_path: Path | None = None) -> list[dict[str, Any]]:
    return load_decision_samples_for_learning(report, report_path=report_path)
