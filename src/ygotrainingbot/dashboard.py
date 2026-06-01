"""Mobile-friendly web dashboard for launching and monitoring training."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ygotrainingbot.deck_visual import deck_to_visual, find_deck_in_pack, load_card_name_index
from ygotrainingbot.format_training import load_format_pack
from ygotrainingbot.gateway_setup import ensure_gateway_dependencies
from ygotrainingbot.ydk import read_ydk, write_ydk
from ygotrainingbot.bot_stats import BotStatsPaths, load_bot_stats, rebuild_bot_stats, write_bot_stats
from ygotrainingbot.human_duels import (
    DEFAULT_CATALOG_DIR,
    build_learning_report,
    catalog_summary,
    import_human_duel_files,
    write_learning_report,
)
from ygotrainingbot.replay_convert import prepare_human_upload_files
from ygotrainingbot.learning import learn_from_report
from ygotrainingbot.paths import TrainingPaths

DEFAULT_DASHBOARD_MAX_DECISIONS = 600
DEFAULT_DASHBOARD_TIMEOUT_SECONDS = 1200.0


def _parse_max_decisions(value: object) -> int:
    """0 means unlimited (gateway skips the per-duel decision cap)."""

    if value is None or value == "":
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_decisions must be a number (0 = unlimited).") from exc
    if parsed < 0:
        raise ValueError("max_decisions cannot be negative.")
    return parsed
DEFAULT_ROSTER_PATH = Path("configs/league-rosters/progression-ycs-regionals.json")
TRAINING_PACK_STEM_DENYLIST = frozenset({"proof-normal-baseline", "edison-2010"})
DEFAULT_CARD_CACHE = Path("data/cards.json")
_CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _parse_banlist_metadata(raw: dict[str, object], *, stem: str) -> dict[str, object]:
    """Derive banlist date labels from pack JSON metadata."""

    source = str(raw.get("banlist_source") or raw.get("description") or stem)
    era = str(raw.get("name") or stem).replace("-", " ").title()
    month = 0
    year = 0
    match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+(\d{4})\b",
        source,
        flags=re.IGNORECASE,
    )
    if match:
        month = MONTHS[match.group(1).lower()]
        year = int(match.group(2))
    else:
        year_match = re.search(r"\b(19|20)\d{2}\b", stem)
        if year_match:
            year = int(year_match.group(0))
    if year and month:
        sort_key = year * 100 + month
        label = f"{year}-{month:02d} · {era}"
    elif year:
        sort_key = year * 100
        label = f"{year} · {era}"
    else:
        sort_key = 0
        label = era
    return {
        "banlist_label": label,
        "banlist_year": year or None,
        "banlist_month": month or None,
        "banlist_sort": sort_key,
        "banlist_source": source,
        "era_name": era,
    }


@dataclass(frozen=True, slots=True)
class DashboardSettings:
    """Runtime paths used by the dashboard."""

    repo_root: Path
    data_dir: Path
    jobs_dir: Path
    bots_dir: Path
    custom_decks_dir: Path
    learned_policy_path: Path
    bracket_output_dir: Path
    edopro_home: Path
    gateway_script: Path
    human_catalog_dir: Path = DEFAULT_CATALOG_DIR
    roster_path: Path = DEFAULT_ROSTER_PATH
    card_cache_path: Path = DEFAULT_CARD_CACHE
    python_executable: str = sys.executable

    @classmethod
    def from_training_paths(
        cls,
        paths: TrainingPaths,
        *,
        gateway_script: Path,
        roster_path: Path = DEFAULT_ROSTER_PATH,
        python_executable: str = sys.executable,
    ) -> DashboardSettings:
        return cls(
            repo_root=paths.repo_root,
            data_dir=paths.data_dir,
            jobs_dir=paths.jobs_dir,
            bots_dir=paths.bots_dir,
            custom_decks_dir=paths.custom_decks_dir,
            learned_policy_path=paths.learned_policy_path,
            bracket_output_dir=paths.bracket_output_dir,
            edopro_home=paths.edopro_home,
            gateway_script=gateway_script,
            human_catalog_dir=paths.human_catalog_dir,
            roster_path=roster_path,
            card_cache_path=paths.card_cache_path,
            python_executable=python_executable,
        )


@dataclass(slots=True)
class TrainingJob:
    """Persisted training job metadata."""

    job_id: str
    job_kind: str
    label: str
    status: str
    games_per_matchup: int
    max_decisions: int
    created_at: float
    started_at: float | None
    finished_at: float | None
    returncode: int | None
    log_path: str
    report_path: str
    summary_path: str
    policy_path: str
    pack: str | None = None
    bot_id: str | None = None
    bot_name: str | None = None
    deck_name: str | None = None
    opponent_bot_id: str | None = None
    opponent_bot_name: str | None = None
    opponent_deck_name: str | None = None
    opponent_custom_deck_id: str | None = None
    opponent_custom_deck_path: str | None = None
    roster_path: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    year: int | None = None
    cycles: int | None = None
    series_per_opponent: int | None = None
    output_dir: str | None = None
    custom_deck_id: str | None = None
    custom_deck_path: str | None = None
    using_learned_policy: str | None = None
    error: str | None = None


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Stop a training subprocess and any child processes (e.g. node gateways)."""

    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class DashboardState:
    """State and job management for dashboard HTTP handlers."""

    def __init__(self, settings: DashboardSettings) -> None:
        self.settings = settings
        self.settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._running_processes: dict[str, subprocess.Popen[str]] = {}
        self._kill_requested: set[str] = set()

    def training_bootstrap(self) -> dict[str, object]:
        """Single payload for the dashboard UI (formats, bots, custom decks)."""

        return {
            "banlists": self.format_packs(),
            "format_packs": self.format_packs(),
            "bots": self.roster_bots(None),
            "opponent_options": self.opponent_options(),
            "rosters": self.rosters(),
            "default_roster": self._display_path(self._default_roster_path()),
            "custom_decks": self.list_custom_decks(),
        }

    def opponent_options(self) -> list[dict[str, object]]:
        options: list[dict[str, object]] = [
            {
                "bot_id": "ai:search-control",
                "name": "Baseline AI",
                "label": "Baseline AI (search-control, no learned weights)",
                "policy": "search-control",
                "is_ai": True,
            }
        ]
        for bot in self.roster_bots(None):
            options.append(
                {
                    "bot_id": str(bot["bot_id"]),
                    "name": str(bot["name"]),
                    "label": f"{bot['name']} ({bot['bot_id']})",
                    "policy": str(bot["policy"]),
                    "is_ai": False,
                }
            )
        return options

    def _meta_deck_entries(self, pack_path: str) -> list[dict[str, object]]:
        pack = load_format_pack(self._resolve_pack_path(pack_path))
        return [
            {
                "id": f"pack:{deck.name}",
                "name": deck.name,
                "label": deck.archetype or deck.name,
                "source": "meta",
                "archetype": deck.archetype,
                "main_count": len(deck.main),
                "extra_count": len(deck.extra),
                "side_count": len(deck.side),
            }
            for deck in pack.decks
        ]

    def _custom_deck_entries(self, bot_id: str) -> list[dict[str, object]]:
        return [
            {
                "id": f"custom:{entry['deck_id']}",
                "name": str(entry["name"]),
                "label": f"{entry['name']} (imported .ydk)",
                "source": "custom-ydk",
                "archetype": "custom",
                "main_count": int(entry.get("main_count", 0)),
                "extra_count": int(entry.get("extra_count", 0)),
            }
            for entry in self.list_custom_decks(bot_id=bot_id)
        ]

    def match_setup(
        self,
        *,
        train_bot_id: str,
        opponent_bot_id: str,
        pack_path: str,
    ) -> dict[str, object]:
        rel_pack = self._display_path(self._resolve_pack_path(pack_path))
        raw = json.loads(self._resolve_pack_path(pack_path).read_text(encoding="utf-8"))
        banlist = _parse_banlist_metadata(raw, stem=Path(pack_path).stem)
        meta_decks = self._meta_deck_entries(pack_path)
        train_bot = self._roster_bot(train_bot_id, None)
        train_decks = [*meta_decks, *self._custom_deck_entries(train_bot_id)]
        opponent_decks: list[dict[str, object]] = [
            {
                "id": "all",
                "name": "all",
                "label": "All meta decks (gauntlet)",
                "source": "gauntlet",
                "archetype": "",
                "main_count": 0,
                "extra_count": 0,
            },
            *meta_decks,
        ]
        if not opponent_bot_id.startswith("ai:"):
            opponent_decks.extend(self._custom_deck_entries(opponent_bot_id))
        return {
            "pack": rel_pack,
            "banlist": banlist,
            "train_bot_id": train_bot_id,
            "opponent_bot_id": opponent_bot_id,
            "train_decks": train_decks,
            "opponent_decks": opponent_decks,
            "train_assigned": self._assigned_deck_name(train_bot, rel_pack),
            "opponent_assigned": None
            if opponent_bot_id.startswith("ai:")
            else self._assigned_deck_name(self._roster_bot(opponent_bot_id, None), rel_pack),
        }

    def bot_decks(self, *, bot_id: str, pack_path: str) -> dict[str, object]:
        return self.match_setup(
            train_bot_id=bot_id,
            opponent_bot_id="ai:search-control",
            pack_path=pack_path,
        )

    def list_custom_decks(self, *, bot_id: str | None = None) -> list[dict[str, object]]:
        root = self._custom_decks_dir()
        if not root.is_dir():
            return []
        entries: list[dict[str, object]] = []
        for path in sorted(root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if bot_id and str(payload.get("bot_id")) != bot_id:
                continue
            entries.append(
                {
                    "deck_id": path.stem,
                    "name": payload.get("name", path.stem),
                    "bot_id": payload.get("bot_id"),
                    "main_count": len(payload.get("main", [])),
                    "extra_count": len(payload.get("extra", [])),
                    "ydk_path": payload.get("ydk_path"),
                }
            )
        return entries

    def import_ydk_deck(
        self,
        *,
        bot_id: str,
        deck_name: str,
        ydk_bytes: bytes,
        filename: str,
    ) -> dict[str, object]:
        if not bot_id:
            raise ValueError("bot_id is required.")
        if not deck_name.strip():
            raise ValueError("deck name is required.")
        self._roster_bot(bot_id, None)

        temp_ydk = self._custom_decks_dir() / f"_upload-{uuid.uuid4().hex[:8]}.ydk"
        temp_ydk.parent.mkdir(parents=True, exist_ok=True)
        temp_ydk.write_bytes(ydk_bytes)
        try:
            zones = read_ydk(temp_ydk)
        finally:
            temp_ydk.unlink(missing_ok=True)

        deck_id = re.sub(r"[^a-z0-9]+", "-", deck_name.lower()).strip("-") or uuid.uuid4().hex[:8]
        deck_id = f"{bot_id}-{deck_id}"
        ydk_path = self._custom_decks_dir() / f"{deck_id}.ydk"
        json_path = self._custom_decks_dir() / f"{deck_id}.json"
        write_ydk(
            ydk_path,
            zones["main"],
            extra=zones["extra"],
            side=zones["side"],
            header_lines=[f"# bot {bot_id}", f"# {deck_name}", f"# source {filename}"],
        )
        payload = {
            "deck_id": deck_id,
            "bot_id": bot_id,
            "name": deck_name.strip(),
            "archetype": "custom",
            "main": list(zones["main"]),
            "extra": list(zones["extra"]),
            "side": list(zones["side"]),
            "ydk_path": self._display_path(ydk_path),
        }
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return payload

    def _custom_decks_dir(self) -> Path:
        self.settings.custom_decks_dir.mkdir(parents=True, exist_ok=True)
        return self.settings.custom_decks_dir

    def _load_custom_deck(self, deck_id: str) -> dict[str, object]:
        path = self._custom_decks_dir() / f"{deck_id}.json"
        if not path.is_file():
            raise ValueError(f"custom deck not found: {deck_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"custom deck {deck_id} is invalid.")
        return payload

    def _card_names(self) -> dict[int, str]:
        cache = self.settings.card_cache_path
        if not cache.is_file():
            cache = self.settings.repo_root / self.settings.card_cache_path
        return load_card_name_index(cache if cache.is_file() else None)

    def _iter_format_pack_paths(self) -> list[Path]:
        root = self.settings.repo_root / "configs/format-packs"
        paths = sorted(root.glob("*.json")) + sorted(root.glob("banlists/*.json"))
        items: list[Path] = []
        for pack_path in paths:
            if pack_path.stem in TRAINING_PACK_STEM_DENYLIST:
                continue
            items.append(pack_path)
        return items

    def format_packs(self) -> list[dict[str, object]]:
        packs: list[dict[str, object]] = []
        for pack_path in self._iter_format_pack_paths():
            raw = json.loads(pack_path.read_text(encoding="utf-8"))
            pack = load_format_pack(pack_path)
            rel = str(pack_path.relative_to(self.settings.repo_root)).replace("\\", "/")
            if raw.get("banlist_label"):
                period = str(raw.get("banlist_period") or pack_path.stem)
                parts = period.split("-")
                year = int(parts[0]) if parts else 0
                month = int(parts[1]) if len(parts) > 1 else 0
                banlist = {
                    "banlist_label": raw["banlist_label"],
                    "banlist_year": year or None,
                    "banlist_month": month or None,
                    "banlist_sort": year * 100 + month,
                    "banlist_source": str(raw.get("banlist_source") or pack.description),
                    "era_name": pack.name,
                    "banlist_period": period,
                }
            else:
                banlist = _parse_banlist_metadata(raw, stem=pack_path.stem)
            packs.append(
                {
                    "name": pack.name,
                    "path": rel,
                    "description": pack.description,
                    "deck_count": len(pack.decks),
                    "default_games": pack.games,
                    "default_max_decisions": (
                        pack.max_decisions
                        if pack.max_decisions > 0
                        else 0
                    ),
                    **banlist,
                    "decks": [
                        {
                            "name": deck.name,
                            "archetype": deck.archetype,
                            "main_count": len(deck.main),
                            "extra_count": len(deck.extra),
                            "side_count": len(deck.side),
                            "id": f"pack:{deck.name}",
                        }
                        for deck in pack.decks
                    ],
                }
            )
        return sorted(packs, key=lambda item: int(item["banlist_sort"]))

    def banlist_meta_gallery(self, pack_path: str) -> dict[str, object]:
        resolved = self._resolve_pack_path(pack_path)
        pack = load_format_pack(resolved)
        names = self._card_names()
        rel = self._display_path(resolved)
        return {
            "pack": rel,
            "banlist_label": json.loads(resolved.read_text(encoding="utf-8")).get("banlist_label"),
            "decks": [deck_to_visual(deck, names=names) for deck in pack.decks],
        }

    def deck_visual(self, *, pack_path: str | None, deck_ref: str) -> dict[str, object]:
        names = self._card_names()
        if deck_ref.startswith("custom:"):
            deck_id = deck_ref.split(":", 1)[1]
            payload = self._load_custom_deck(deck_id)
            return deck_to_visual(payload, names=names)
        deck_name = deck_ref.split(":", 1)[1] if deck_ref.startswith("pack:") else deck_ref
        if not pack_path:
            raise ValueError("pack is required for format meta decks.")
        deck = find_deck_in_pack(self._resolve_pack_path(pack_path), deck_name)
        return deck_to_visual(deck, names=names)

    def rosters(self) -> list[dict[str, object]]:
        roster_dir = self.settings.repo_root / "configs/league-rosters"
        items: list[dict[str, object]] = []
        for path in sorted(roster_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            bots = payload.get("bots", [])
            items.append(
                {
                    "path": str(path.relative_to(self.settings.repo_root)).replace("\\", "/"),
                    "name": path.stem,
                    "bot_count": len(bots) if isinstance(bots, list) else 0,
                }
            )
        return items

    def roster_bots(self, roster_path: str | None = None) -> list[dict[str, object]]:
        path = self._resolve_roster_path(roster_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        bots_payload = payload.get("bots", [])
        if not isinstance(bots_payload, list):
            raise ValueError(f"roster at {path} must include a bots list.")
        bots: list[dict[str, object]] = []
        for bot in bots_payload:
            if not isinstance(bot, dict):
                continue
            assigned = dict(bot.get("assigned_decks", {}))
            bots.append(
                {
                    "bot_id": str(bot.get("bot_id", "")),
                    "name": str(bot.get("name", bot.get("bot_id", ""))),
                    "policy": str(bot.get("policy", "heuristic")),
                    "characteristics": str(bot.get("characteristics", "")),
                    "assigned_decks": assigned,
                    "policy_path": self._display_path(self._bot_policy_path(str(bot["bot_id"]))),
                    "has_policy": self._bot_policy_path(str(bot["bot_id"])).is_file(),
                }
            )
        return bots

    def jobs(self) -> list[dict[str, object]]:
        jobs = [self._read_job_meta(path) for path in self.settings.jobs_dir.glob("*/meta.json")]
        return sorted(jobs, key=lambda job: float(job["created_at"]), reverse=True)

    def job(self, job_id: str) -> dict[str, object]:
        meta_path = self._job_dir(job_id) / "meta.json"
        if not meta_path.exists():
            raise KeyError(job_id)
        return self._read_job_meta(meta_path)

    def start_job(self, pack_path: str, games_per_matchup: int, max_decisions: int) -> TrainingJob:
        return self.start_training(
            {
                "job_kind": "format-pack",
                "pack": pack_path,
                "games_per_matchup": games_per_matchup,
                "max_decisions": max_decisions,
            }
        )

    def start_training(self, payload: dict[str, object]) -> TrainingJob:
        job_kind = str(payload.get("job_kind", "format-pack"))
        games_per_matchup = int(payload.get("games_per_matchup", 5))
        max_decisions = _parse_max_decisions(payload.get("max_decisions", 0))
        if games_per_matchup < 1:
            raise ValueError("games_per_matchup must be at least 1.")

        bot_id = str(payload["bot_id"]) if payload.get("bot_id") else None
        bot_name = None
        opponent_bot_id = str(payload.get("opponent_bot_id") or "ai:search-control")
        opponent_bot_name = None
        pack_path = str(payload["pack"]) if payload.get("pack") else None
        deck_name, custom_deck_id, custom_deck_path = self._resolve_deck_selection(
            str(payload["deck_name"]) if payload.get("deck_name") else None
        )
        opponent_deck_value = str(payload.get("opponent_deck_name") or "")
        opponent_deck_name, opponent_custom_deck_id, opponent_custom_deck_path = (
            self._resolve_deck_selection(opponent_deck_value or None)
        )
        opponent_gauntlet = opponent_deck_value == "all"
        roster_rel = str(payload.get("roster_path") or self._display_path(self._default_roster_path()))

        if job_kind in {"bot-spar", "yearly-bracket", "yearly-bracket-loop"}:
            if not bot_id:
                raise ValueError(f"{job_kind} requires bot_id.")
            bot = self._roster_bot(bot_id, roster_rel)
            bot_name = str(bot["name"])
            self._ensure_bot_policy(bot_id, roster_rel)

        if job_kind == "bot-spar":
            if opponent_bot_id.startswith("ai:"):
                opponent_bot_name = "Baseline AI"
            else:
                opponent = self._roster_bot(opponent_bot_id, roster_rel)
                opponent_bot_name = str(opponent["name"])
                self._ensure_bot_policy(opponent_bot_id, roster_rel)
            if not deck_name and pack_path:
                deck_name = self._assigned_deck_name(bot, pack_path)
            if not opponent_gauntlet and not opponent_deck_name and pack_path and not opponent_bot_id.startswith("ai:"):
                opponent = self._roster_bot(opponent_bot_id, roster_rel)
                opponent_deck_name = self._assigned_deck_name(opponent, pack_path)
            if not deck_name:
                raise ValueError("bot-spar requires a deck for the bot you are training.")
            if not opponent_gauntlet and not opponent_deck_name and not opponent_custom_deck_id:
                raise ValueError("bot-spar requires an opponent deck or the all-meta gauntlet.")

        label = self._job_label(
            job_kind,
            bot_name=bot_name,
            opponent_bot_name=opponent_bot_name,
            pack_path=pack_path,
            deck_name=deck_name,
            opponent_deck_name=None if opponent_gauntlet else opponent_deck_name,
            start_year=payload.get("start_year"),
            end_year=payload.get("end_year"),
            year=payload.get("year"),
        )

        if pack_path and job_kind in {"format-pack", "bot-spar", "format-matrix"}:
            self._resolve_pack_path(pack_path)

        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        output_dir = payload.get("output_dir")
        if output_dir:
            resolved_output = str(output_dir)
        elif job_kind == "yearly-bracket":
            resolved_output = self._display_path(
                self.settings.bracket_output_dir / f"dashboard-bracket-{job_id[:15]}"
            )
        elif job_kind == "yearly-bracket-loop":
            resolved_output = self._display_path(
                self.settings.bracket_output_dir / f"dashboard-loop-{job_id[:15]}"
            )
        else:
            resolved_output = self._display_path(job_dir)

        job = TrainingJob(
            job_id=job_id,
            job_kind=job_kind,
            label=label,
            status="queued",
            games_per_matchup=games_per_matchup,
            max_decisions=max_decisions,
            created_at=time.time(),
            started_at=None,
            finished_at=None,
            returncode=None,
            log_path=self._display_path(job_dir / "training.log"),
            report_path=self._display_path(job_dir / "report.json"),
            summary_path=self._display_path(job_dir / "learning-summary.txt"),
            policy_path=self._display_path(job_dir / "learned-policy.json"),
            pack=pack_path,
            bot_id=bot_id,
            bot_name=bot_name,
            deck_name=deck_name,
            opponent_bot_id=None if opponent_bot_id.startswith("ai:") else opponent_bot_id,
            opponent_bot_name=opponent_bot_name,
            opponent_deck_name=None if opponent_gauntlet else opponent_deck_name,
            roster_path=roster_rel,
            start_year=int(payload["start_year"]) if payload.get("start_year") is not None else None,
            end_year=int(payload["end_year"]) if payload.get("end_year") is not None else None,
            year=int(payload["year"]) if payload.get("year") is not None else None,
            cycles=int(payload["cycles"]) if payload.get("cycles") is not None else None,
            series_per_opponent=int(payload["series_per_opponent"])
            if payload.get("series_per_opponent") is not None
            else None,
            output_dir=resolved_output,
            custom_deck_id=custom_deck_id,
            custom_deck_path=custom_deck_path,
            opponent_custom_deck_id=opponent_custom_deck_id,
            opponent_custom_deck_path=opponent_custom_deck_path,
            using_learned_policy=self._display_path(self._bot_policy_path(bot_id))
            if bot_id and self._bot_policy_path(bot_id).exists()
            else (
                self._display_path(self._global_policy_path())
                if self._global_policy_path().exists()
                else None
            ),
        )
        self._write_job(job)
        thread = threading.Thread(target=self._run_job, args=(job,), daemon=True)
        thread.start()
        return job

    def log_text(self, job_id: str) -> str:
        log_path = self._job_dir(job_id) / "training.log"
        if not log_path.exists():
            return ""
        return log_path.read_text(encoding="utf-8", errors="replace")

    def duel_log_text(self, job_id: str) -> str:
        duel_log_path = self._job_dir(job_id) / "duel-live.log"
        if not duel_log_path.exists():
            return ""
        return duel_log_path.read_text(encoding="utf-8", errors="replace")

    def kill_all_jobs(self) -> dict[str, object]:
        """Terminate every running training subprocess and mark jobs cancelled."""

        killed: list[str] = []
        with self._lock:
            for job_id in self._running_processes:
                self._kill_requested.add(job_id)
            processes = dict(self._running_processes)

        for job_id, process in processes.items():
            if process.poll() is None:
                _terminate_process_tree(process)
            if job_id not in killed:
                killed.append(job_id)

        for meta_path in self.settings.jobs_dir.glob("*/meta.json"):
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            job_id = str(data.get("job_id", meta_path.parent.name))
            if data.get("status") not in {"running", "queued"}:
                continue
            if job_id not in killed:
                killed.append(job_id)
            self._cancel_job_meta(job_id)

        return {"killed": killed, "count": len(killed)}

    def report(self, job_id: str) -> dict[str, object]:
        report_path = self._job_dir(job_id) / "report.json"
        if not report_path.exists():
            raise KeyError(job_id)
        return json.loads(report_path.read_text(encoding="utf-8"))

    def summary_text(self, job_id: str) -> str:
        summary_path = self._job_dir(job_id) / "learning-summary.txt"
        if not summary_path.exists():
            return "Learning summary is not ready yet."
        return summary_path.read_text(encoding="utf-8", errors="replace")

    def human_catalog(self) -> dict[str, Any]:
        catalog_dir = self._human_catalog_dir()
        return catalog_summary(catalog_dir)

    def upload_human_replays(
        self,
        files: list[tuple[str, bytes]],
        *,
        study_agent: str | None = None,
        format_name: str | None = None,
    ) -> dict[str, Any]:
        if not files:
            raise ValueError("upload at least one replay file (.yrpX, .yrp, or .json).")

        prepared, convert_errors, converted_count = prepare_human_upload_files(
            files,
            study_agent=study_agent or None,
            format_name=format_name or None,
        )
        if not prepared and convert_errors:
            return {
                "imported": 0,
                "skipped": [],
                "errors": convert_errors,
                "converted_from_replay": converted_count,
                "catalog": self.human_catalog(),
            }

        result = import_human_duel_files(prepared, catalog_dir=self._human_catalog_dir())
        result.errors.extend(convert_errors)
        self.refresh_bot_stats()
        return {
            "imported": len(result.imported),
            "skipped": result.skipped,
            "errors": result.errors,
            "converted_from_replay": converted_count,
            "duels": [
                {
                    "duel_id": entry.duel_id,
                    "format": entry.format,
                    "study_agent": entry.study_agent,
                    "decision_count": entry.decision_count,
                }
                for entry in result.imported
            ],
            "catalog": self.human_catalog(),
            "bot_stats": self.bot_stats(),
        }

    def learn_from_human_replays(
        self,
        *,
        study_agent: str | None = None,
        format_filter: str | None = None,
    ) -> dict[str, Any]:
        catalog_dir = self._human_catalog_dir()
        report = build_learning_report(
            catalog_dir,
            study_agent=study_agent or None,
            format_filter=format_filter or None,
        )
        report_path = write_learning_report(catalog_dir, report)
        summary_path = catalog_dir / "learning-summary.txt"
        policy_path = self._global_policy_path()
        _analysis, english = learn_from_report(report_path, policy_path)
        summary_path.write_text(english, encoding="utf-8")
        self.refresh_bot_stats()
        return {
            "report_path": self._display_path(report_path),
            "summary_path": self._display_path(summary_path),
            "policy_path": self._display_path(policy_path),
            "total_games": report.get("total_games"),
            "total_decisions": report.get("total_traced_decisions"),
            "format": report.get("format"),
            "bot_agent": report.get("bot_agent"),
            "summary": english,
            "bot_stats": self.bot_stats(),
        }

    def human_learning_summary(self) -> str:
        summary_path = self._human_catalog_dir() / "learning-summary.txt"
        if not summary_path.is_file():
            return "No human replay learning summary yet. Upload replays and click Learn."
        return summary_path.read_text(encoding="utf-8", errors="replace")

    def human_learning_report(self) -> dict[str, Any]:
        report_path = self._human_catalog_dir() / "human-learning-report.json"
        if not report_path.is_file():
            return {
                "ready": False,
                "message": "No human replay learning report yet. Upload replays and click Learn.",
            }
        return json.loads(report_path.read_text(encoding="utf-8"))

    def _bot_stats_paths(self) -> BotStatsPaths:
        roster = self.settings.roster_path
        if not roster.is_absolute():
            roster = (self.settings.repo_root / roster).resolve()
        catalog = self._human_catalog_dir()
        return BotStatsPaths(
            repo_root=self.settings.repo_root,
            jobs_dir=self.settings.jobs_dir,
            bots_dir=self.settings.bots_dir,
            human_catalog_dir=catalog,
            roster_path=roster,
            stats_path=self.settings.data_dir / "bot-training-stats.json",
        )

    def refresh_bot_stats(self) -> dict[str, Any]:
        return self.persist_bot_stats()

    def bot_stats(self, *, refresh: bool = False) -> dict[str, Any]:
        paths = self._bot_stats_paths()
        if refresh:
            snapshot = rebuild_bot_stats(paths)
            try:
                write_bot_stats(paths)
                return load_bot_stats(paths.stats_path)
            except OSError:
                return snapshot
        if paths.stats_path.is_file():
            cached = load_bot_stats(paths.stats_path)
            if cached.get("updated_at") is not None:
                return cached
        snapshot = rebuild_bot_stats(paths)
        try:
            write_bot_stats(paths)
        except OSError:
            pass
        return snapshot

    def persist_bot_stats(self) -> dict[str, Any]:
        paths = self._bot_stats_paths()
        snapshot = rebuild_bot_stats(paths)
        try:
            write_bot_stats(paths)
            return load_bot_stats(paths.stats_path)
        except OSError:
            return snapshot

    def _human_catalog_dir(self) -> Path:
        path = self.settings.human_catalog_dir
        if not path.is_absolute():
            path = (self.settings.repo_root / path).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run_job(self, job: TrainingJob) -> None:
        import os

        job_dir = self._job_dir(job.job_id)
        log_path = job_dir / "training.log"
        duel_log_path = job_dir / "duel-live.log"
        report_path = job_dir / "report.json"

        try:
            job.status = "running"
            job.started_at = time.time()
            self._write_job(job)
            duel_log_path.write_text(
                f"[{time.strftime('%H:%M:%S')}] Training job {job.job_id} started ({job.label})\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["YGOTRAIN_DUEL_LIVE_LOG"] = str(duel_log_path.resolve())
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            with log_path.open("a", encoding="utf-8") as log:
                self._ensure_gateway_dependencies(log)
                self._ensure_edopro_home(log)
                command = self._build_cli_command(job, report_path=report_path)
                log.write("$ " + " ".join(command) + "\n")
                log.flush()
                process = subprocess.Popen(
                    command,
                    cwd=self.settings.repo_root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )
                with self._lock:
                    self._running_processes[job.job_id] = process
                cancelled = False
                while process.poll() is None:
                    if job.job_id in self._kill_requested:
                        _terminate_process_tree(process)
                        job.status = "cancelled"
                        job.error = "Stopped by user"
                        job.returncode = -1
                        log.write("\nJob stopped by user (kill all).\n")
                        log.flush()
                        cancelled = True
                        break
                    time.sleep(0.25)
                if not cancelled:
                    job.returncode = process.returncode
                    if process.returncode == 0:
                        self._post_process_job(job, report_path=report_path, job_dir=job_dir, log=log)
                        job.status = "completed"
                    else:
                        job.status = "failed"
        except Exception as exc:  # pragma: no cover - defensive job boundary
            job.status = "failed"
            job.error = str(exc)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\nERROR: {exc}\n")
        finally:
            with self._lock:
                self._running_processes.pop(job.job_id, None)
                self._kill_requested.discard(job.job_id)
            job.finished_at = time.time()
            if duel_log_path.exists():
                with duel_log_path.open("a", encoding="utf-8") as duel_log:
                    duel_log.write(
                        f"\n[{time.strftime('%H:%M:%S')}] Job finished: status={job.status}"
                        f"{f', error={job.error}' if job.error else ''}\n",
                    )
            self._write_job(job)

    def _build_cli_command(self, job: TrainingJob, *, report_path: Path) -> list[str]:
        base = [
            self.settings.python_executable,
            "-m",
            "ygotrainingbot.cli",
        ]
        timeout = str(DEFAULT_DASHBOARD_TIMEOUT_SECONDS)
        edopro = str(self.settings.edopro_home)
        gateway = str(self.settings.gateway_script)

        if job.job_kind == "format-matrix":
            packs = ["configs/format-packs/goat-2005.json", "configs/format-packs/edison-2010.json"]
            if job.pack:
                packs = [job.pack]
            return base + [
                "test-format-matrix",
                "--packs",
                *packs,
                "--edopro-home",
                edopro,
                "--gateway-script",
                gateway,
                "--games-per-matchup",
                str(job.games_per_matchup),
                "--max-decisions",
                str(job.max_decisions),
                "--timeout-seconds",
                timeout,
                "--output",
                str(report_path),
            ]

        if job.job_kind == "yearly-bracket":
            roster = str(self.settings.repo_root / job.roster_path) if job.roster_path else str(self._default_roster_path())
            out = str(self.settings.repo_root / job.output_dir) if job.output_dir else str(report_path.parent)
            return base + [
                "run-yearly-bracket",
                "--roster-path",
                roster,
                "--edopro-home",
                edopro,
                "--gateway-script",
                gateway,
                "--start-year",
                str(job.start_year or 2010),
                "--end-year",
                str(job.end_year or 2025),
                "--series-per-opponent",
                str(job.series_per_opponent or job.games_per_matchup),
                "--max-decisions",
                str(job.max_decisions),
                "--timeout-seconds",
                timeout,
                "--ethan-bot-id",
                str(job.bot_id or "bot-01"),
                "--output-dir",
                out,
            ]

        if job.job_kind == "yearly-bracket-loop":
            roster = str(self.settings.repo_root / job.roster_path) if job.roster_path else str(self._default_roster_path())
            out = str(self.settings.repo_root / job.output_dir) if job.output_dir else str(report_path.parent)
            return base + [
                "run-yearly-bracket-loop",
                "--roster-path",
                roster,
                "--edopro-home",
                edopro,
                "--gateway-script",
                gateway,
                "--year",
                str(job.year or 2010),
                "--cycles",
                str(job.cycles or 5),
                "--series-per-opponent",
                str(job.series_per_opponent or job.games_per_matchup),
                "--max-decisions",
                str(job.max_decisions),
                "--timeout-seconds",
                timeout,
                "--ethan-bot-id",
                str(job.bot_id or "bot-01"),
                "--output-dir",
                out,
            ]

        # format-pack and bot-spar
        if not job.pack:
            raise ValueError(f"{job.job_kind} requires a format pack.")
        command = base + [
            "train-format-pack",
            "--pack",
            job.pack,
            "--edopro-home",
            edopro,
            "--gateway-script",
            gateway,
            "--games-per-matchup",
            str(job.games_per_matchup),
            "--max-decisions",
            str(job.max_decisions),
            "--timeout-seconds",
            timeout,
            "--output",
            str(report_path),
        ]
        if job.job_kind == "bot-spar":
            bot = self._roster_bot(str(job.bot_id), str(job.roster_path))
            policy_path = self._bot_policy_path(str(job.bot_id))
            command.extend(["--agent-a-policy", str(bot["policy"])])
            command.extend(["--agent-a-weights", str(policy_path)])
            opponent_id = job.opponent_bot_id or "ai:search-control"
            if opponent_id.startswith("ai:"):
                command.extend(["--agent-b-policy", "search-control"])
            else:
                opponent = self._roster_bot(opponent_id, str(job.roster_path))
                opponent_policy = self._bot_policy_path(opponent_id)
                command.extend(["--agent-b-policy", str(opponent["policy"])])
                if opponent_policy.is_file():
                    command.extend(["--agent-b-weights", str(opponent_policy)])
            if job.custom_deck_path:
                command.extend([
                    "--custom-deck-a-file",
                    str(self.settings.repo_root / job.custom_deck_path),
                ])
            elif job.deck_name:
                command.extend(["--deck-a-name", job.deck_name])
            if job.opponent_custom_deck_path:
                command.extend([
                    "--custom-deck-b-file",
                    str(self.settings.repo_root / job.opponent_custom_deck_path),
                ])
            elif job.opponent_deck_name:
                command.extend(["--deck-b-name", job.opponent_deck_name])
        elif job.using_learned_policy:
            weights = str(self.settings.repo_root / job.using_learned_policy)
            command.extend([
                "--agent-a-weights",
                weights,
                "--agent-b-weights",
                weights,
            ])
        return command

    def _post_process_job(
        self,
        job: TrainingJob,
        *,
        report_path: Path,
        job_dir: Path,
        log: Any,
    ) -> None:
        learn_target = job_dir / "learned-policy.json"
        if job.job_kind in {"yearly-bracket", "yearly-bracket-loop"}:
            out = self.settings.repo_root / job.output_dir if job.output_dir else job_dir
            tournament_report = out / "tournament-report.json"
            if tournament_report.is_file():
                report_path.write_text(tournament_report.read_text(encoding="utf-8"), encoding="utf-8")
            bot_policy = out / "bots" / str(job.bot_id) / "policy.json"
            if bot_policy.is_file():
                learn_target.write_text(bot_policy.read_text(encoding="utf-8"), encoding="utf-8")
                log.write(f"\n$ copied bot policy from {bot_policy}\n")
            log.write("\n$ yearly bracket complete — see output_dir for season logs\n")
            return

        if not report_path.is_file():
            return
        _analysis, english = learn_from_report(report_path, learn_target)
        (job_dir / "learning-summary.txt").write_text(english, encoding="utf-8")
        if job.job_kind == "bot-spar" and job.bot_id:
            bot_policy = self._bot_policy_path(str(job.bot_id))
            bot_policy.parent.mkdir(parents=True, exist_ok=True)
            bot_policy.write_text(learn_target.read_text(encoding="utf-8"), encoding="utf-8")
            log.write(f"\n$ updated bot policy at {bot_policy}\n")
        else:
            self._global_policy_path().write_text(
                learn_target.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            log.write(f"\n$ updated {self._display_path(self._global_policy_path())}\n")
        log.write("\n$ generated learning-summary.txt\n")
        try:
            self.persist_bot_stats()
            log.write("\n$ refreshed bot-training-stats.json\n")
        except Exception as exc:  # pragma: no cover - stats must not fail jobs
            log.write(f"\n$ bot stats refresh skipped: {exc}\n")

    def _job_label(
        self,
        job_kind: str,
        *,
        bot_name: str | None,
        opponent_bot_name: str | None = None,
        pack_path: str | None,
        deck_name: str | None,
        opponent_deck_name: str | None = None,
        start_year: object,
        end_year: object,
        year: object,
    ) -> str:
        if job_kind == "yearly-bracket":
            return f"Season {start_year}-{end_year} · {bot_name or 'Yugi'}"
        if job_kind == "yearly-bracket-loop":
            return f"Season drill {year} · {bot_name or 'Yugi'}"
        if job_kind == "format-matrix":
            return "Gateway health check"
        if job_kind == "bot-spar":
            vs = opponent_bot_name or "opponent"
            if opponent_deck_name:
                return f"{bot_name} vs {vs} · {deck_name} vs {opponent_deck_name}"
            return f"{bot_name} gauntlet · {deck_name} vs all meta"
        return f"Meta sweep · {pack_path or 'unknown'}"

    def _resolve_deck_selection(
        self,
        deck_value: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        if not deck_value or deck_value == "all":
            return None, None, None
        if deck_value.startswith("pack:"):
            return deck_value.split(":", 1)[1], None, None
        if deck_value.startswith("custom:"):
            deck_id = deck_value.split(":", 1)[1]
            custom_path = self._display_path(self._custom_decks_dir() / f"{deck_id}.json")
            deck_name = str(self._load_custom_deck(deck_id).get("name", deck_id))
            return deck_name, deck_id, custom_path
        return deck_value, None, None

    def _default_roster_path(self) -> Path:
        path = self.settings.roster_path
        if path.is_absolute():
            return path
        return (self.settings.repo_root / path).resolve()

    def _resolve_roster_path(self, roster_path: str | None) -> Path:
        candidate = self._default_roster_path() if not roster_path else (self.settings.repo_root / roster_path)
        resolved = candidate.resolve()
        roster_root = (self.settings.repo_root / "configs/league-rosters").resolve()
        if roster_root not in resolved.parents or resolved.suffix != ".json":
            raise ValueError("roster must be a JSON file under configs/league-rosters.")
        if not resolved.is_file():
            raise ValueError(f"roster does not exist: {roster_path}")
        return resolved

    def _roster_bot(self, bot_id: str, roster_path: str | None) -> dict[str, object]:
        for bot in self.roster_bots(roster_path):
            if bot["bot_id"] == bot_id:
                return bot
        raise ValueError(f"bot {bot_id!r} not found in roster.")

    def _assigned_deck_name(self, bot: dict[str, object], pack_path: str) -> str | None:
        assigned = dict(bot.get("assigned_decks", {}))
        entry = assigned.get(pack_path)
        if isinstance(entry, dict):
            return str(entry.get("name") or "") or None
        return None

    def _ensure_bot_policy(self, bot_id: str, roster_path: str | None) -> Path:
        path = self._bot_policy_path(bot_id)
        if path.is_file():
            return path
        roster_file = self._resolve_roster_path(roster_path)
        payload = json.loads(roster_file.read_text(encoding="utf-8"))
        for bot in payload.get("bots", []):
            if not isinstance(bot, dict) or str(bot.get("bot_id")) != bot_id:
                continue
            weights = dict(bot.get("initial_weights", {}))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"tag_weights": weights, "observations": 0}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return path
        raise ValueError(f"cannot initialize policy for {bot_id}")

    def _bot_policy_path(self, bot_id: str) -> Path:
        return self.settings.bots_dir / bot_id / "policy.json"

    def _ensure_gateway_dependencies(self, log: Any) -> None:
        ensure_gateway_dependencies(self.settings.repo_root, log=log)

    def _ensure_edopro_home(self, log: Any) -> None:
        if (self.settings.edopro_home / "cards.cdb").exists():
            return
        command = ["scripts/bootstrap_edopro_home.sh", str(self.settings.edopro_home)]
        log.write("$ " + " ".join(command) + "\n")
        log.flush()
        subprocess.run(
            command,
            cwd=self.settings.repo_root,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )

    def _resolve_pack_path(self, pack_path: str) -> Path:
        candidate = (self.settings.repo_root / pack_path).resolve()
        packs_root = (self.settings.repo_root / "configs/format-packs").resolve()
        if packs_root not in candidate.parents or candidate.suffix != ".json":
            raise ValueError("pack must be a JSON file under configs/format-packs.")
        if not candidate.exists():
            raise ValueError(f"pack does not exist: {pack_path}")
        return candidate

    def _job_dir(self, job_id: str) -> Path:
        if "/" in job_id or "\\" in job_id or ".." in job_id:
            raise ValueError("invalid job id.")
        return self.settings.jobs_dir / job_id

    def _global_policy_path(self) -> Path:
        return self.settings.learned_policy_path

    def _display_path(self, path: Path) -> str:
        try:
            display = str(path.relative_to(self.settings.repo_root))
        except ValueError:
            display = str(path)
        return display.replace("\\", "/")

    def _read_job_meta(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_job(self, job: TrainingJob) -> None:
        with self._lock:
            meta_path = self._job_dir(job.job_id) / "meta.json"
            meta_path.write_text(json.dumps(asdict(job), indent=2, sort_keys=True), encoding="utf-8")

    def _cancel_job_meta(self, job_id: str, *, error: str = "Stopped by user") -> None:
        meta_path = self._job_dir(job_id) / "meta.json"
        if not meta_path.exists():
            return
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if data.get("status") not in {"running", "queued"}:
            return
        data["status"] = "cancelled"
        data["error"] = error
        data["finished_at"] = time.time()
        data["returncode"] = -1
        meta_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        job_dir = meta_path.parent
        duel_log_path = job_dir / "duel-live.log"
        if duel_log_path.exists():
            with duel_log_path.open("a", encoding="utf-8") as duel_log:
                duel_log.write(f"\n[{time.strftime('%H:%M:%S')}] Job cancelled: {error}\n")
        log_path = job_dir / "training.log"
        if log_path.exists():
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\nJob cancelled: {error}\n")


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP routes for the dashboard UI and JSON API."""

    state: DashboardState

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            self._handle_get()
        except Exception as exc:  # pragma: no cover - HTTP safety net
            self._log_handler_error("GET", exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            self._handle_post()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pragma: no cover - HTTP safety net
            self._log_handler_error("POST", exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_get(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(DASHBOARD_HTML)
        elif path == "/replays":
            self._send_html(REPLAYS_HTML)
        elif path == "/api/training-bootstrap":
            self._send_json(self.state.training_bootstrap())
        elif path == "/api/format-packs":
            self._send_json({"format_packs": self.state.format_packs()})
        elif path == "/api/rosters":
            self._send_json({"rosters": self.state.rosters()})
        elif path == "/api/rosters/default/bots":
            self._send_json({"bots": self.state.roster_bots(None)})
        elif path.startswith("/api/rosters/") and path.endswith("/bots"):
            roster_path = path.removeprefix("/api/rosters/").removesuffix("/bots")
            if roster_path == "default":
                self._send_json({"bots": self.state.roster_bots(None)})
            else:
                self._send_json({"bots": self.state.roster_bots(roster_path)})
        elif path == "/api/match-setup":
            query = parse_qs(urlparse(self.path).query)
            train_bot_id = (query.get("train_bot_id") or query.get("bot_id") or [""])[0]
            opponent_bot_id = (query.get("opponent_bot_id") or ["ai:search-control"])[0]
            pack_path = (query.get("pack") or [""])[0]
            if not train_bot_id or not pack_path:
                raise ValueError("train_bot_id and pack query parameters are required.")
            self._send_json(
                self.state.match_setup(
                    train_bot_id=train_bot_id,
                    opponent_bot_id=opponent_bot_id,
                    pack_path=pack_path,
                )
            )
        elif path == "/api/banlist-decks":
            query = parse_qs(urlparse(self.path).query)
            pack_path = (query.get("pack") or [""])[0]
            if not pack_path:
                raise ValueError("pack query parameter is required.")
            self._send_json(self.state.banlist_meta_gallery(pack_path))
        elif path == "/api/decks/visual":
            query = parse_qs(urlparse(self.path).query)
            pack_path = (query.get("pack") or [None])[0]
            deck_ref = (query.get("deck") or query.get("deck_id") or [""])[0]
            if not deck_ref:
                raise ValueError("deck query parameter is required.")
            self._send_json(self.state.deck_visual(pack_path=pack_path, deck_ref=deck_ref))
        elif path == "/api/bot-decks":
            query = parse_qs(urlparse(self.path).query)
            bot_id = (query.get("bot_id") or [""])[0]
            pack_path = (query.get("pack") or [""])[0]
            if not bot_id or not pack_path:
                raise ValueError("bot_id and pack query parameters are required.")
            self._send_json(self.state.bot_decks(bot_id=bot_id, pack_path=pack_path))
        elif path == "/api/human-duels":
            self._send_json(self.state.human_catalog())
        elif path == "/api/human-duels/summary":
            self._send_text(self.state.human_learning_summary())
        elif path == "/api/human-duels/report":
            self._send_json(self.state.human_learning_report())
        elif path == "/api/bot-stats":
            query = parse_qs(urlparse(self.path).query)
            refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
            self._send_json(self.state.bot_stats(refresh=refresh))
        elif path == "/api/jobs":
            self._send_json({"jobs": self.state.jobs()})
        elif path.startswith("/api/jobs/") and path.endswith("/duel-log"):
            job_id = path.split("/")[3]
            self._send_text(self.state.duel_log_text(job_id))
        elif path.startswith("/api/jobs/") and path.endswith("/log"):
            job_id = path.split("/")[3]
            self._send_text(self.state.log_text(job_id))
        elif path.startswith("/api/jobs/") and path.endswith("/report"):
            job_id = path.split("/")[3]
            self._send_json(self.state.report(job_id))
        elif path.startswith("/api/jobs/") and path.endswith("/summary"):
            job_id = path.split("/")[3]
            self._send_text(self.state.summary_text(job_id))
        elif path.startswith("/api/jobs/"):
            job_id = path.split("/")[3]
            self._send_json({"job": self.state.job(job_id)})
        else:
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def _handle_post(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/jobs/kill-all":
            self._send_json(self.state.kill_all_jobs())
            return
        if path == "/api/jobs":
            payload = self._read_json()
            if payload.get("pack") and not payload.get("job_kind"):
                job = self.state.start_job(
                    pack_path=str(payload.get("pack", "")),
                    games_per_matchup=int(payload.get("games_per_matchup", 5)),
                    max_decisions=_parse_max_decisions(payload.get("max_decisions", 0)),
                )
            else:
                job = self.state.start_training(payload)
            self._send_json({"job": asdict(job)}, status=HTTPStatus.CREATED)
            return
        if path == "/api/decks/import-ydk":
            fields, files = self._read_multipart_form()
            bot_id = fields.get("bot_id", "").strip()
            deck_name = fields.get("deck_name", "").strip()
            ydk_file = next((item for item in files if item[0].lower().endswith(".ydk")), None)
            if not ydk_file:
                raise ValueError("upload a .ydk deck file.")
            payload = self.state.import_ydk_deck(
                bot_id=bot_id,
                deck_name=deck_name or Path(ydk_file[0]).stem,
                ydk_bytes=ydk_file[1],
                filename=ydk_file[0],
            )
            self._send_json({"deck": payload, "custom_decks": self.state.list_custom_decks(bot_id=bot_id)}, status=HTTPStatus.CREATED)
            return
        if path == "/api/human-duels/upload":
            fields, files = self._read_multipart_form()
            if not files:
                raise ValueError("choose at least one replay file (.yrpX, .yrp, or .json).")
            payload = self.state.upload_human_replays(
                files,
                study_agent=fields.get("study_agent") or None,
                format_name=fields.get("format") or None,
            )
            status = HTTPStatus.CREATED if payload["imported"] else HTTPStatus.BAD_REQUEST
            self._send_json(payload, status=status)
            return
        if path == "/api/human-duels/learn":
            payload = self._read_json()
            study_agent = payload.get("study_agent")
            format_filter = payload.get("format")
            result = self.state.learn_from_human_replays(
                study_agent=str(study_agent) if study_agent else None,
                format_filter=str(format_filter) if format_filter else None,
            )
            self._send_json(result)
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length) if length else b"{}"
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object.")
        return payload

    def _read_multipart_form(self) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        if not content_type.startswith("multipart/form-data"):
            raise ValueError("requests must use multipart/form-data.")
        return _parse_multipart_form(body, content_type)

    def _read_upload_files(self) -> list[tuple[str, bytes]]:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        if not content_type.startswith("multipart/form-data"):
            raise ValueError("upload requests must use multipart/form-data.")
        return _parse_multipart_files(body, content_type)

    def _log_handler_error(self, method: str, exc: BaseException) -> None:
        path = urlparse(self.path).path
        print(f"dashboard {method} {path} failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    def _write_response_body(self, body: bytes) -> None:
        try:
            self.wfile.write(body)
        except _CLIENT_DISCONNECT_ERRORS:
            return

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        except _CLIENT_DISCONNECT_ERRORS:
            return
        self._write_response_body(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        except _CLIENT_DISCONNECT_ERRORS:
            return
        self._write_response_body(body)

    def _send_text(self, text: str) -> None:
        body = text.encode("utf-8")
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        except _CLIENT_DISCONNECT_ERRORS:
            return
        self._write_response_body(body)


def _parse_multipart_form(body: bytes, content_type: str) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    """Parse text fields and uploaded files from a multipart/form-data body."""

    match = re.search(r"boundary=(.+)", content_type, flags=re.IGNORECASE)
    if not match:
        raise ValueError("multipart request missing boundary.")
    boundary = match.group(1).strip().strip('"').encode("utf-8")
    delimiter = b"--" + boundary
    fields: dict[str, str] = {}
    files: list[tuple[str, bytes]] = []

    for part in body.split(delimiter):
        if not part or part in (b"--", b"--\r\n"):
            continue
        chunk = part
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        if not chunk.strip():
            continue

        header_blob, _, data = chunk.partition(b"\r\n\r\n")
        if not header_blob:
            continue
        headers = header_blob.decode("utf-8", errors="replace")
        if data.endswith(b"\r\n"):
            data = data[:-2]

        name_match = re.search(r'name="([^"]*)"', headers)
        field_name = name_match.group(1) if name_match else ""
        if "filename=" in headers:
            filename_match = re.search(r'filename="([^"]*)"', headers)
            if not filename_match:
                filename_match = re.search(r"filename=([^;\r\n]+)", headers)
            filename = (filename_match.group(1) if filename_match else field_name or "upload.bin").strip()
            files.append((filename, data))
        elif field_name:
            fields[field_name] = data.decode("utf-8")

    return fields, files


def _parse_multipart_files(body: bytes, content_type: str) -> list[tuple[str, bytes]]:
    """Parse uploaded files from a multipart/form-data body."""

    _, files = _parse_multipart_form(body, content_type)
    return list(files)


def _detect_repo_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "configs" / "format-packs").is_dir() and (path / "src" / "ygotrainingbot").is_dir():
            return path
    return candidate


def run_dashboard(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    repo_root: Path | None = None,
    data_dir: Path | None = None,
    edopro_home: Path | None = None,
    human_catalog_dir: Path | None = None,
) -> None:
    """Run the training dashboard HTTP server."""

    root = _detect_repo_root(repo_root)
    paths = TrainingPaths.resolve(
        root,
        data_dir=data_dir,
        edopro_home=edopro_home,
        human_catalog_dir=human_catalog_dir,
    )
    paths.ensure_directories()
    settings = DashboardSettings.from_training_paths(
        paths,
        gateway_script=root / "gateways/edopro-ocgcore/gateway.mjs",
    )
    DashboardHandler.state = DashboardState(settings)
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"YGO Training Console at http://{host}:{port}")
    print(f"Training data: {paths.data_dir}")
    print("Modes: bot spar, format pack, yearly bracket, bracket loop, format matrix")
    server.serve_forever()


def main() -> int:
    """Console entry point for the dashboard."""

    parser = argparse.ArgumentParser(prog="ygotrain-dashboard")
    parser.add_argument("--host", default=os.environ.get("YGOTRAIN_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Persistent training root (jobs, bots, policies). Defaults to YGOTRAIN_DATA_DIR or .ygotrain/",
    )
    parser.add_argument("--edopro-home", type=Path, default=None)
    parser.add_argument(
        "--human-catalog-dir",
        type=Path,
        default=None,
        help="Directory for imported human replay JSON logs.",
    )
    args = parser.parse_args()
    run_dashboard(
        host=args.host,
        port=args.port,
        repo_root=args.repo_root,
        data_dir=args.data_dir,
        edopro_home=args.edopro_home,
        human_catalog_dir=args.human_catalog_dir,
    )
    return 0


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>YGO Training Console</title>
  <style>
    :root { color-scheme: dark; --bg:#0f172a; --muted:#94a3b8; --text:#e5e7eb; --accent:#38bdf8; --ok:#22c55e; --bad:#ef4444; --warn:#f59e0b; --panel:#111827; --line:rgba(148,163,184,.18); }
    * { box-sizing:border-box; }
    body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:linear-gradient(180deg,#020617,var(--bg)); color:var(--text); min-height:100vh; }
    header { padding:16px 18px 0; max-width:1400px; margin:0 auto; display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; }
    h1 { margin:0; font-size:22px; }
    h2 { margin:0 0 10px; font-size:15px; text-transform:uppercase; letter-spacing:.06em; color:var(--accent); }
    .sub { color:var(--muted); margin:0; line-height:1.45; font-size:13px; }
    main { display:flex; flex-direction:column; gap:14px; padding:14px 18px 24px; max-width:1400px; margin:0 auto; }
    section, .panel { background:rgba(17,24,39,.94); border:1px solid var(--line); border-radius:14px; padding:14px; }
    label { display:block; color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; margin:0 0 6px; }
    select,input,button { width:100%; border-radius:10px; border:1px solid rgba(148,163,184,.25); padding:10px 12px; background:#020617; color:var(--text); font-size:14px; }
    button.primary { background:var(--accent); color:#082f49; border:0; font-weight:800; cursor:pointer; }
    button.secondary { background:#334155; color:var(--text); border:0; font-weight:700; cursor:pointer; }
    button:disabled { opacity:.55; cursor:wait; }
    .hidden { display:none !important; }
    a { color:var(--accent); }

    .top-bar { display:grid; grid-template-columns:minmax(200px,280px) minmax(180px,240px) 1fr; gap:14px; align-items:end; }
    .mode-hint { margin:0; color:var(--muted); font-size:13px; line-height:1.5; padding:10px 12px; background:#020617; border:1px solid var(--line); border-radius:10px; min-height:42px; }

    .bot-row { display:grid; grid-template-columns:1fr 1fr; gap:14px; min-height:420px; }
    .bot-panel { display:flex; flex-direction:column; gap:10px; min-height:0; }
    .bot-meta { color:var(--muted); font-size:12px; margin:4px 0 0; line-height:1.4; }
    .import-row { display:grid; grid-template-columns:1fr auto; gap:8px; align-items:end; }
    .import-row input[type=file] { font-size:12px; padding:8px; }
    .deck-preview-box { flex:1; min-height:180px; overflow:auto; border:1px solid var(--line); border-radius:10px; padding:10px; background:#020617; }
    .preview-empty { color:var(--muted); font-size:13px; text-align:center; padding:24px 12px; }

    .bottom-stage { min-height:320px; display:flex; flex-direction:column; gap:14px; }
    .stage-toolbar { display:flex; flex-wrap:wrap; gap:12px; align-items:end; }
    .stage-toolbar > div { flex:1; min-width:140px; }
    .stage-body { flex:1; min-height:160px; border:1px dashed rgba(148,163,184,.25); border-radius:12px; padding:14px; background:#020617; overflow:auto; }
    .stage-title { margin:0 0 8px; font-size:13px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
    .stage-split { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
    @media (max-width:960px) { .stage-split { grid-template-columns:1fr; } }

    .meta-deck-grid { display:grid; gap:10px; grid-template-columns:repeat(auto-fill, minmax(120px, 1fr)); }
    .meta-deck-card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:8px; cursor:pointer; transition:border-color .15s; }
    .meta-deck-card:hover, .meta-deck-card.selected { border-color:var(--accent); }
    .meta-deck-card img { width:100%; border-radius:6px; aspect-ratio:59/86; object-fit:cover; background:#1e293b; }
    .meta-deck-card strong { display:block; font-size:11px; margin-top:6px; line-height:1.25; }
    .meta-deck-card span { color:var(--muted); font-size:10px; }

    .card-grid { display:grid; gap:6px; grid-template-columns:repeat(auto-fill, minmax(56px, 1fr)); margin-top:6px; }
    .card-tile { background:var(--panel); border:1px solid rgba(148,163,184,.12); border-radius:6px; padding:3px; text-align:center; position:relative; }
    .card-tile img { width:100%; border-radius:4px; display:block; }
    .card-tile .count { position:absolute; top:3px; right:3px; background:rgba(15,23,42,.92); color:var(--accent); font-size:9px; font-weight:800; padding:1px 4px; border-radius:999px; }
    .card-tile .label { font-size:8px; color:var(--muted); margin-top:2px; line-height:1.15; max-height:2.3em; overflow:hidden; }
    .zone-title { font-size:10px; color:var(--muted); margin:10px 0 2px; text-transform:uppercase; letter-spacing:.04em; }

    .bracket-placeholder { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-top:8px; }
    .bracket-col { border:1px solid var(--line); border-radius:10px; padding:10px; min-height:120px; }
    .bracket-col h3 { margin:0 0 8px; font-size:12px; color:var(--accent); }
    .bracket-slot { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:8px; margin-bottom:6px; font-size:12px; color:var(--muted); }

    .job { border:1px solid var(--line); border-radius:10px; padding:10px; margin-top:6px; background:var(--panel); cursor:pointer; }
    .job:hover { border-color:rgba(56,189,248,.35); }
    .status { display:inline-block; padding:2px 7px; border-radius:999px; font-size:10px; font-weight:800; background:#334155; }
    .status.running { background:rgba(56,189,248,.2); color:var(--accent); }
    .status.cancelled { background:rgba(245,158,11,.2); color:var(--warn); }
    button.danger { background:var(--bad); color:#fff; border:0; font-weight:700; cursor:pointer; padding:6px 12px; border-radius:8px; font-size:12px; }
    button.danger:disabled { opacity:0.5; cursor:not-allowed; }
    .completed { background:rgba(34,197,94,.18); color:var(--ok); }
    .failed { background:rgba(239,68,68,.18); color:var(--bad); }
    .running,.queued { background:rgba(56,189,248,.18); color:var(--accent); }
    pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:280px; overflow:auto; background:#020617; border-radius:10px; padding:10px; border:1px solid var(--line); font-size:11px; margin:0; }
    .pill { display:inline-block; padding:2px 7px; border-radius:999px; font-size:10px; background:#1e293b; color:var(--muted); margin-right:4px; }
    .hint { color:var(--muted); font-size:12px; line-height:1.45; margin:4px 0 0; }
    #message { min-height:1.2em; font-size:13px; margin:0; }

    .stats-table { width:100%; border-collapse:collapse; font-size:14px; margin-top:10px; }
    .stats-table th, .stats-table td { text-align:left; padding:8px 10px; border-bottom:1px solid rgba(148,163,184,.14); }
    .stats-table th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
    .stats-table tr:hover td { background:rgba(56,189,248,.06); }
    .stats-updated { font-size:13px; color:var(--muted); margin:0 0 8px; }
    @media (max-width:900px) {
      .top-bar { grid-template-columns:1fr; }
      .bot-row { grid-template-columns:1fr; }
      .stats-table { font-size:12px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>YGO Training Console</h1>
    <p class="sub"><a href="/replays">Human replays →</a></p>
  </header>

  <main>
    <section id="bot-stats-section">
      <h2>Bot training stats</h2>
      <p class="stats-updated" id="bot-stats-updated">Loading…</p>
      <div style="overflow-x:auto;">
        <table class="stats-table" id="bot-stats-table">
          <thead>
            <tr>
              <th>Bot</th>
              <th>Training duels</th>
              <th>Human duels</th>
              <th>Sessions</th>
              <th>Policy updates</th>
              <th>Avg turns</th>
              <th>Avg actions</th>
              <th>Last trained</th>
            </tr>
          </thead>
          <tbody id="bot-stats-body"><tr><td colspan="8">Loading…</td></tr></tbody>
        </table>
      </div>
    </section>

    <section class="top-bar">
      <div id="banlist-block">
        <label for="banlist">Banlist / format</label>
        <select id="banlist"></select>
      </div>
      <div>
        <label for="mode-select">Training mode</label>
        <select id="mode-select">
          <option value="bot-spar">Focused duel</option>
          <option value="format-pack">Meta sweep</option>
          <option value="yearly-bracket">Full season (2010–2025)</option>
          <option value="yearly-bracket-loop">Single-year drill</option>
          <option value="format-matrix">Gateway health check</option>
        </select>
      </div>
      <div>
        <label>Mode guide</label>
        <p class="mode-hint" id="mode-hint">Pick a banlist, configure both bots below, then launch from the bottom panel.</p>
      </div>
    </section>

    <div class="bot-row" id="bot-row">
      <section class="bot-panel" id="bot-panel-train">
        <h2>Bot 1 — to train</h2>
        <div>
          <label for="bot">Bot</label>
          <select id="bot"></select>
          <p class="bot-meta" id="bot-meta">—</p>
        </div>
        <div id="train-deck-block">
          <label for="deck">Deck</label>
          <select id="deck"></select>
        </div>
        <div id="train-import-block">
          <label>Import .ydk</label>
          <div class="import-row">
            <input id="ydk-file" type="file" accept=".ydk" />
            <button type="button" class="secondary" id="import-ydk">Import</button>
          </div>
          <input id="ydk-name" type="text" placeholder="Deck name (optional)" style="margin-top:8px;" />
        </div>
        <div class="deck-preview-box" id="train-preview-box">
          <p class="preview-empty" id="train-preview-empty">Select a deck to preview cards.</p>
          <div id="train-preview-content" class="hidden">
            <span class="pill" id="preview-title-train"></span>
            <p class="zone-title">Main</p>
            <div id="preview-main-train" class="card-grid"></div>
            <p class="zone-title" id="preview-extra-label-train">Extra</p>
            <div id="preview-extra-train" class="card-grid"></div>
            <p class="zone-title" id="preview-side-label-train">Side</p>
            <div id="preview-side-train" class="card-grid"></div>
          </div>
        </div>
      </section>

      <section class="bot-panel" id="bot-panel-opponent">
        <h2>Bot 2 — train against</h2>
        <div id="opponent-bot-wrap">
          <label for="opponent-bot">Bot</label>
          <select id="opponent-bot"></select>
          <p class="bot-meta" id="opponent-meta">—</p>
        </div>
        <div id="opponent-deck-block">
          <label for="opponent-deck">Deck</label>
          <select id="opponent-deck"></select>
        </div>
        <div id="opponent-import-block">
          <label>Import .ydk</label>
          <div class="import-row">
            <input id="opponent-ydk-file" type="file" accept=".ydk" />
            <button type="button" class="secondary" id="import-opponent-ydk">Import</button>
          </div>
          <input id="opponent-ydk-name" type="text" placeholder="Deck name (optional)" style="margin-top:8px;" />
        </div>
        <div class="deck-preview-box" id="opponent-preview-box">
          <p class="preview-empty" id="opponent-preview-empty">Select a deck to preview cards.</p>
          <div id="opponent-preview-content" class="hidden">
            <span class="pill" id="preview-title-opponent"></span>
            <p class="zone-title">Main</p>
            <div id="preview-main-opponent" class="card-grid"></div>
            <p class="zone-title" id="preview-extra-label-opponent">Extra</p>
            <div id="preview-extra-opponent" class="card-grid"></div>
            <p class="zone-title" id="preview-side-label-opponent">Side</p>
            <div id="preview-side-opponent" class="card-grid"></div>
          </div>
        </div>
      </section>
    </div>

    <section class="bottom-stage">
      <div id="stage-spar" class="stage-toolbar">
        <div><label for="games">Games per matchup</label><input id="games" type="number" min="1" value="10" /></div>
        <div><label for="decisions">Max decisions / game (0 = unlimited)</label><input id="decisions" type="number" min="0" value="0" /></div>
        <div style="flex:0 0 auto; min-width:180px;"><label>&nbsp;</label><button class="primary" id="start">Start training job</button></div>
      </div>

      <div id="stage-meta" class="hidden">
        <p class="stage-title">Meta sweep — all deck pairings at this banlist</p>
        <div id="meta-deck-grid" class="meta-deck-grid"></div>
        <p class="hint" style="margin-top:10px;">Runs every top meta deck vs every other. Updates the shared global policy.</p>
        <button class="primary" id="start-meta" style="margin-top:12px; max-width:280px;">Start meta sweep</button>
      </div>

      <div id="stage-bracket" class="hidden">
        <p class="stage-title">Full season bracket</p>
        <div class="cols-2" style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
          <div><label for="start-year">Start year</label><input id="start-year" type="number" value="2010" /></div>
          <div><label for="end-year">End year</label><input id="end-year" type="number" value="2025" /></div>
        </div>
        <label for="series">Series games per opponent</label>
        <input id="series" type="number" min="1" value="10" />
        <div class="bracket-placeholder">
          <div class="bracket-col"><h3>Regular season</h3><div class="bracket-slot">Your bot vs each roster opponent</div><div class="bracket-slot">Era decks assigned per year</div></div>
          <div class="bracket-col"><h3>Learning</h3><div class="bracket-slot">All bots update between rounds</div></div>
          <div class="bracket-col"><h3>Output</h3><div class="bracket-slot">Standings + per-bot policies</div></div>
        </div>
        <button class="primary" id="start-bracket" style="margin-top:12px; max-width:280px;">Start full season</button>
      </div>

      <div id="stage-loop" class="hidden">
        <p class="stage-title">Single-year drill</p>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
          <div><label for="loop-year">Year</label><input id="loop-year" type="number" value="2010" /></div>
          <div><label for="cycles">Learning cycles</label><input id="cycles" type="number" min="1" value="5" /></div>
        </div>
        <label for="loop-series">Series per opponent</label>
        <input id="loop-series" type="number" min="1" value="10" />
        <button class="primary" id="start-loop" style="margin-top:12px; max-width:280px;">Start year drill</button>
      </div>

      <div id="stage-health" class="hidden">
        <p class="stage-title">Gateway health check</p>
        <p class="hint">Smoke test that EDOPro can finish duels at the selected banlist. Almost no learning — use to verify setup.</p>
        <button class="primary" id="start-health" style="margin-top:12px; max-width:280px;">Run health check</button>
      </div>

      <p id="message"></p>

      <div class="stage-split">
        <div>
          <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:6px;">
            <p class="stage-title" style="margin:0;">Jobs</p>
            <button type="button" class="danger" id="kill-all-jobs" title="Stop all running training jobs">Kill all</button>
          </div>
          <div class="stage-body" style="min-height:140px; max-height:220px;">
            <div id="jobs"><p class="sub">No jobs yet.</p></div>
          </div>
        </div>
        <div>
          <p class="stage-title">Live duel feed <span class="pill" id="selected-job">none</span></p>
          <div class="stage-body" style="min-height:200px; padding:0;">
            <pre id="duel-log">Select a job to watch duels in real time.</pre>
          </div>
          <p class="stage-title" style="margin-top:10px; font-size:12px;">Process log</p>
          <div class="stage-body" style="min-height:100px; padding:0;">
            <pre id="log" style="font-size:11px; opacity:0.85;">CLI output appears here.</pre>
          </div>
        </div>
      </div>
    </section>
  </main>

  <script>
    let banlists = [], bots = [], opponents = [], selectedJob = null;
    const botSel = document.querySelector('#bot');
    const opponentBotSel = document.querySelector('#opponent-bot');
    const banlistSel = document.querySelector('#banlist');
    const deckSel = document.querySelector('#deck');
    const opponentDeckSel = document.querySelector('#opponent-deck');
    const modeSel = document.querySelector('#mode-select');
    const msgEl = document.querySelector('#message');

    async function json(url, options) {
      const res = await fetch(url, options);
      const text = await res.text();
      if (!res.ok) throw new Error(text || res.statusText);
      return text ? JSON.parse(text) : {};
    }

    function mode() { return modeSel.value || 'bot-spar'; }

    function currentBanlist() { return banlists.find(p => p.path === banlistSel.value); }

    const MODE_HINTS = {
      'bot-spar': 'Focused duel — your bot plays N games vs one opponent. Only your bot\'s policy is updated.',
      'format-pack': 'Meta sweep — run every meta deck vs every other at this banlist. Updates the shared global policy.',
      'yearly-bracket': 'Full season — simulate 2010–2025 bracket. Your bot faces every roster opponent with era decks. All bots learn.',
      'yearly-bracket-loop': 'Single-year drill — replay one calendar year multiple times with learning between cycles.',
      'format-matrix': 'Gateway health check — quick smoke test that EDOPro can finish duels at this banlist.',
    };

    function syncModePanels() {
      const m = mode();
      const isSpar = m === 'bot-spar';
      const isSeason = m === 'yearly-bracket' || m === 'yearly-bracket-loop';
      document.querySelector('#mode-hint').textContent = MODE_HINTS[m] || '';
      document.querySelector('#bot-row').classList.toggle('hidden', m === 'format-pack' || m === 'format-matrix');
      document.querySelector('#bot-panel-opponent').classList.toggle('hidden', !isSpar);
      document.querySelector('#train-deck-block').classList.toggle('hidden', !isSpar);
      document.querySelector('#train-import-block').classList.toggle('hidden', !isSpar);
      document.querySelector('#opponent-deck-block').classList.toggle('hidden', !isSpar);
      document.querySelector('#opponent-import-block').classList.toggle('hidden', !isSpar);
      document.querySelector('#stage-spar').classList.toggle('hidden', !isSpar);
      document.querySelector('#stage-meta').classList.toggle('hidden', m !== 'format-pack');
      document.querySelector('#stage-bracket').classList.toggle('hidden', m !== 'yearly-bracket');
      document.querySelector('#stage-loop').classList.toggle('hidden', m !== 'yearly-bracket-loop');
      document.querySelector('#stage-health').classList.toggle('hidden', m !== 'format-matrix');
      document.querySelector('#banlist-block').classList.toggle('hidden', isSeason);
      opponentBotSel.disabled = !isSpar;
      deckSel.disabled = !isSpar;
      opponentDeckSel.disabled = !isSpar;
      if (m === 'format-pack') loadBanlistGallery();
    }

    function deckOptionLabel(d) {
      if (d.source === 'gauntlet') return d.label;
      const side = d.side_count ? ` · ${d.side_count} side` : '';
      const extra = d.extra_count ? ` · ${d.extra_count} extra` : '';
      return `${d.label} (${d.main_count} main${extra}${side})`;
    }

    function selectAssigned(selectEl, assignedName) {
      if (!assignedName) return false;
      for (const opt of selectEl.options) {
        if (opt.value === `pack:${assignedName}` || opt.value === assignedName) {
          opt.selected = true;
          return true;
        }
      }
      return false;
    }

    function restoreDeckSelection(selectEl, preferredValue, assignedName, fallbackIndex) {
      if (preferredValue) {
        for (const opt of selectEl.options) {
          if (opt.value === preferredValue) {
            opt.selected = true;
            return;
          }
        }
      }
      if (!selectAssigned(selectEl, assignedName) && fallbackIndex != null && selectEl.options.length > fallbackIndex) {
        selectEl.selectedIndex = fallbackIndex;
      }
    }

    function renderCardGrid(el, cards) {
      el.innerHTML = (cards || []).map(card =>
        `<div class="card-tile" title="${card.name}">
          ${card.count > 1 ? `<span class="count">×${card.count}</span>` : ''}
          <img src="${card.image_url}" alt="${card.name}" loading="lazy" onerror="this.style.opacity='0.35'" />
          <div class="label">${card.name}</div>
        </div>`
      ).join('');
    }

    async function showDeckPreview(deckRef, side) {
      const prefix = side === 'opponent' ? 'opponent' : 'train';
      const emptyEl = document.querySelector(`#${prefix}-preview-empty`);
      const contentEl = document.querySelector(`#${prefix}-preview-content`);
      if (!deckRef || deckRef === 'all') {
        emptyEl.classList.remove('hidden');
        contentEl.classList.add('hidden');
        return;
      }
      try {
        const data = await json(
          `/api/decks/visual?pack=${encodeURIComponent(banlistSel.value)}&deck=${encodeURIComponent(deckRef)}`
        );
        document.querySelector(`#preview-title-${prefix}`).textContent = data.archetype || data.name;
        renderCardGrid(document.querySelector(`#preview-main-${prefix}`), data.main);
        const extraEl = document.querySelector(`#preview-extra-${prefix}`);
        const sideEl = document.querySelector(`#preview-side-${prefix}`);
        const hasExtra = !!(data.extra && data.extra.length);
        const hasSide = !!(data.side && data.side.length);
        document.querySelector(`#preview-extra-label-${prefix}`).classList.toggle('hidden', !hasExtra);
        extraEl.classList.toggle('hidden', !hasExtra);
        document.querySelector(`#preview-side-label-${prefix}`).classList.toggle('hidden', !hasSide);
        sideEl.classList.toggle('hidden', !hasSide);
        renderCardGrid(extraEl, data.extra);
        renderCardGrid(sideEl, data.side);
        emptyEl.classList.add('hidden');
        contentEl.classList.remove('hidden');
      } catch (err) {
        msgEl.textContent = String(err);
      }
    }

    async function loadBanlistGallery() {
      const packPath = banlistSel.value;
      const grid = document.querySelector('#meta-deck-grid');
      if (!packPath || mode() !== 'format-pack') {
        grid.innerHTML = '';
        return;
      }
      grid.innerHTML = '<p class="hint">Loading top meta decks…</p>';
      try {
        const data = await json(`/api/banlist-decks?pack=${encodeURIComponent(packPath)}`);
        grid.innerHTML = (data.decks || []).map(deck => {
          const cover = deck.main?.[0]?.image_url || '';
          return `<div class="meta-deck-card" data-deck="pack:${deck.name}">
            <img src="${cover}" alt="${deck.archetype || deck.name}" loading="lazy" onerror="this.style.opacity='0.35'" />
            <strong>${deck.archetype || deck.name}</strong>
            <span>${deck.main_count} main · ${deck.extra_count} extra · ${deck.side_count || 0} side</span>
          </div>`;
        }).join('');
        grid.querySelectorAll('.meta-deck-card').forEach(node => {
          node.addEventListener('click', () => {
            grid.querySelectorAll('.meta-deck-card').forEach(item => item.classList.remove('selected'));
            node.classList.add('selected');
            const deckRef = node.dataset.deck;
            if (deckRef) deckSel.value = deckRef;
            showDeckPreview(deckRef, 'train');
          });
        });
        if (data.decks?.length) {
          grid.querySelector('.meta-deck-card')?.classList.add('selected');
        }
      } catch (err) {
        grid.innerHTML = `<p class="hint">${String(err)}</p>`;
      }
    }

    async function populateMatchSetup(options = {}) {
      const preserveTrain = options.preserveTrain !== false;
      const preserveOpponent = options.preserveOpponent !== false;
      const packPath = banlistSel.value;
      if (!packPath || mode() !== 'bot-spar') {
        deckSel.innerHTML = '';
        opponentDeckSel.innerHTML = '';
        return;
      }
      const prevTrainDeck = preserveTrain ? deckSel.value : '';
      const prevOpponentDeck = preserveOpponent ? opponentDeckSel.value : '';
      try {
        const data = await json(
          `/api/match-setup?train_bot_id=${encodeURIComponent(botSel.value)}`
          + `&opponent_bot_id=${encodeURIComponent(opponentBotSel.value)}`
          + `&pack=${encodeURIComponent(packPath)}`
        );
        deckSel.innerHTML = (data.train_decks || []).map(d =>
          `<option value="${d.id}">${deckOptionLabel(d)}</option>`
        ).join('');
        opponentDeckSel.innerHTML = (data.opponent_decks || []).map(d =>
          `<option value="${d.id}">${deckOptionLabel(d)}</option>`
        ).join('');
        restoreDeckSelection(deckSel, prevTrainDeck, data.train_assigned, null);
        restoreDeckSelection(
          opponentDeckSel,
          prevOpponentDeck,
          data.opponent_assigned,
          data.opponent_assigned ? null : 1,
        );
        if (deckSel.value) showDeckPreview(deckSel.value, 'train');
        if (opponentDeckSel.value) showDeckPreview(opponentDeckSel.value, 'opponent');
      } catch (err) {
        msgEl.textContent = String(err);
        deckSel.innerHTML = '';
        opponentDeckSel.innerHTML = '';
      }
    }

    function updateBotMeta() {
      const bot = bots.find(b => b.bot_id === botSel.value);
      document.querySelector('#bot-meta').textContent = bot
        ? `${bot.policy} · ${bot.characteristics}${bot.has_policy ? ' · policy loaded' : ' · initial weights'}`
        : '—';
      const opp = opponents.find(b => b.bot_id === opponentBotSel.value);
      document.querySelector('#opponent-meta').textContent = opp
        ? (opp.is_ai ? 'Fixed baseline opponent — does not learn' : `${opp.policy}${opp.bot_id !== botSel.value ? '' : ' · same bot'}`)
        : '—';
    }

    banlistSel.addEventListener('change', () => {
      const pack = currentBanlist();
      if (pack) {
        document.querySelector('#games').value = Math.min(pack.default_games, 25);
        document.querySelector('#decisions').value = pack.default_max_decisions ?? 0;
      }
      populateMatchSetup({ preserveTrain: false, preserveOpponent: false });
      loadBanlistGallery();
    });
    botSel.addEventListener('change', () => {
      updateBotMeta();
      populateMatchSetup({ preserveTrain: false, preserveOpponent: true });
    });
    opponentBotSel.addEventListener('change', () => {
      updateBotMeta();
      populateMatchSetup({ preserveTrain: true, preserveOpponent: false });
    });
    deckSel.addEventListener('change', () => showDeckPreview(deckSel.value, 'train'));
    opponentDeckSel.addEventListener('change', () => showDeckPreview(opponentDeckSel.value, 'opponent'));
    modeSel.addEventListener('change', () => {
      syncModePanels();
      populateMatchSetup({ preserveTrain: false, preserveOpponent: false });
      loadBanlistGallery();
    });

    async function loadData() {
      const data = await json('/api/training-bootstrap');
      if (data.error) throw new Error(data.error);
      banlists = data.banlists || data.format_packs || [];
      bots = data.bots || [];
      opponents = data.opponent_options || [];
      if (!banlists.length) throw new Error('No banlist training packs found under configs/format-packs.');
      if (!bots.length) throw new Error('No bots found in the default roster.');
      banlistSel.innerHTML = banlists.map(p =>
        `<option value="${p.path}">${p.banlist_label || p.name}</option>`
      ).join('');
      botSel.innerHTML = bots.map(b => `<option value="${b.bot_id}">${b.name}</option>`).join('');
      opponentBotSel.innerHTML = opponents.map(o =>
        `<option value="${o.bot_id}">${o.label || o.name}</option>`
      ).join('');
      if (opponentBotSel.options.length > 1) opponentBotSel.selectedIndex = 1;
      updateBotMeta();
      syncModePanels();
      await populateMatchSetup();
      await loadBanlistGallery();
    }

    async function importYdk(which) {
      const isOpponent = which === 'opponent';
      const fileInput = document.querySelector(isOpponent ? '#opponent-ydk-file' : '#ydk-file');
      const nameInput = document.querySelector(isOpponent ? '#opponent-ydk-name' : '#ydk-name');
      const btn = document.querySelector(isOpponent ? '#import-opponent-ydk' : '#import-ydk');
      const targetBot = isOpponent ? opponentBotSel : botSel;
      const targetDeckSel = isOpponent ? opponentDeckSel : deckSel;
      const file = fileInput.files?.[0];
      if (!file) {
        msgEl.textContent = 'Choose a .ydk file first.';
        return;
      }
      btn.disabled = true;
      msgEl.textContent = 'Importing deck…';
      try {
        const form = new FormData();
        form.append('bot_id', targetBot.value);
        form.append('deck_name', nameInput.value || file.name.replace(/\.ydk$/i, ''));
        form.append('ydk', file, file.name);
        const res = await fetch('/api/decks/import-ydk', { method: 'POST', body: form });
        const text = await res.text();
        if (!res.ok) throw new Error(text || res.statusText);
        const data = JSON.parse(text);
        msgEl.textContent = `Imported: ${data.deck.name}`;
        fileInput.value = '';
        nameInput.value = '';
        await populateMatchSetup({
          preserveTrain: isOpponent,
          preserveOpponent: !isOpponent,
        });
        for (const opt of targetDeckSel.options) {
          if (opt.value === `custom:${data.deck.deck_id}`) { opt.selected = true; break; }
        }
        showDeckPreview(targetDeckSel.value, isOpponent ? 'opponent' : 'train');
      } catch (err) {
        msgEl.textContent = String(err);
      } finally {
        btn.disabled = false;
      }
    }

    function parseMaxDecisionsInput() {
      const raw = document.querySelector('#decisions').value;
      if (raw === '') {
        return 0;
      }
      const parsed = Number(raw);
      if (!Number.isFinite(parsed) || parsed < 0) {
        throw new Error('Max decisions must be 0 (unlimited) or a non-negative number.');
      }
      return Math.trunc(parsed);
    }

    function buildPayload() {
      const m = mode();
      const payload = {
        job_kind: m,
        bot_id: botSel.value,
        max_decisions: parseMaxDecisionsInput(),
      };
      if (m === 'yearly-bracket') {
        payload.start_year = Number(document.querySelector('#start-year').value);
        payload.end_year = Number(document.querySelector('#end-year').value);
        payload.series_per_opponent = Number(document.querySelector('#series').value);
        payload.games_per_matchup = payload.series_per_opponent;
        return payload;
      }
      if (m === 'yearly-bracket-loop') {
        payload.year = Number(document.querySelector('#loop-year').value);
        payload.cycles = Number(document.querySelector('#cycles').value);
        payload.series_per_opponent = Number(document.querySelector('#loop-series').value);
        payload.games_per_matchup = payload.series_per_opponent;
        return payload;
      }
      payload.pack = banlistSel.value;
      payload.games_per_matchup = Number(document.querySelector('#games').value);
      if (m === 'bot-spar') {
        payload.deck_name = deckSel.value;
        payload.opponent_bot_id = opponentBotSel.value;
        payload.opponent_deck_name = opponentDeckSel.value;
      }
      return payload;
    }

    async function startJob() {
      const btn = document.querySelector('#start');
      btn.disabled = true;
      msgEl.textContent = 'Starting job…';
      try {
        parseMaxDecisionsInput();
        const data = await json('/api/jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(buildPayload()),
        });
        selectedJob = data.job.job_id;
        document.querySelector('#selected-job').textContent = selectedJob;
        msgEl.textContent = `Started: ${data.job.label}`;
        await loadJobs();
        await loadLog();
      } catch (err) {
        msgEl.textContent = String(err);
      } finally {
        btn.disabled = false;
      }
    }

    async function loadJobs() {
      const data = await json('/api/jobs');
      const el = document.querySelector('#jobs');
      if (!data.jobs.length) { el.innerHTML = '<p class="sub">No jobs yet.</p>'; return; }
      el.innerHTML = data.jobs.map(j => {
        const links = j.status === 'completed'
          ? `<a href="/api/jobs/${j.job_id}/summary" target="_blank">Report</a>`
          : (j.error ? `<span style="color:var(--bad)">${j.error}</span>` : '');
        return `<div class="job" data-job="${j.job_id}">
          <span class="status ${j.status}">${j.status}</span>
          <span class="pill">${j.job_kind || 'format-pack'}</span>
          <strong>${j.label || j.pack || j.job_id}</strong>
          <p class="sub" style="margin:4px 0 0;font-size:11px;">${j.bot_name || ''}${j.opponent_bot_name ? ' vs ' + j.opponent_bot_name : ''} · ${j.games_per_matchup} games</p>
          ${links}
        </div>`;
      }).join('');
      el.querySelectorAll('.job').forEach(node => node.addEventListener('click', () => {
        selectedJob = node.dataset.job;
        document.querySelector('#selected-job').textContent = selectedJob;
        loadLog();
      }));
    }

    async function loadLog() {
      if (!selectedJob) return;
      const [duelRes, jobRes] = await Promise.all([
        fetch(`/api/jobs/${selectedJob}/duel-log`),
        fetch(`/api/jobs/${selectedJob}/log`),
      ]);
      const duelPre = document.querySelector('#duel-log');
      const jobPre = document.querySelector('#log');
      const duelText = await duelRes.text();
      const jobText = await jobRes.text();
      duelPre.textContent = duelText || '(waiting for first duel — job may still be starting)';
      duelPre.scrollTop = duelPre.scrollHeight;
      const tail = jobText.length > 5000 ? jobText.slice(-5000) : jobText;
      jobPre.textContent = tail || '(empty process log)';
      jobPre.scrollTop = jobPre.scrollHeight;
    }

    async function killAllJobs() {
      if (!confirm('Stop all running training jobs? Duels in progress will be interrupted.')) {
        return;
      }
      const btn = document.querySelector('#kill-all-jobs');
      btn.disabled = true;
      try {
        const data = await json('/api/jobs/kill-all', { method: 'POST' });
        msgEl.textContent = data.count
          ? `Stopped ${data.count} job(s).`
          : 'No running jobs to stop.';
        await loadJobs();
        await loadLog();
      } catch (err) {
        msgEl.textContent = String(err);
      } finally {
        btn.disabled = false;
      }
    }

    document.querySelector('#kill-all-jobs').addEventListener('click', killAllJobs);
    document.querySelector('#start').addEventListener('click', startJob);
    document.querySelector('#start-meta').addEventListener('click', startJob);
    document.querySelector('#start-bracket').addEventListener('click', startJob);
    document.querySelector('#start-loop').addEventListener('click', startJob);
    document.querySelector('#start-health').addEventListener('click', startJob);
    document.querySelector('#import-ydk').addEventListener('click', () => importYdk('train'));
    document.querySelector('#import-opponent-ydk').addEventListener('click', () => importYdk('opponent'));
    function renderBotStats(data) {
      const updated = document.querySelector('#bot-stats-updated');
      const body = document.querySelector('#bot-stats-body');
      if (!updated || !body) return;
      const stamp = data.updated_at ? `Updated ${data.updated_at}` : 'Not updated yet';
      const totals = data.totals || {};
      updated.textContent = `${stamp} · ${totals.training_duels || 0} training duels · ${totals.total_decisions || 0} decisions logged`;
      const bots = data.bots || [];
      if (!bots.length) {
        body.innerHTML = '<tr><td colspan="8">No bot stats yet — run a training job or upload a replay.</td></tr>';
        return;
      }
      body.innerHTML = bots.map(bot => `
        <tr>
          <td><strong>${bot.name}</strong><br/><span class="hint">${bot.bot_id}</span></td>
          <td>${bot.training_duels ?? 0}</td>
          <td>${bot.human_duels ?? 0}</td>
          <td>${bot.training_sessions ?? 0}</td>
          <td>${bot.policy_updates ?? 0}</td>
          <td>${bot.avg_duel_turns != null ? bot.avg_duel_turns : '—'}</td>
          <td>${bot.avg_decisions_per_game != null ? bot.avg_decisions_per_game : '—'}</td>
          <td>${bot.last_trained_at ? bot.last_trained_at.replace('T', ' ').replace('Z', ' UTC') : '—'}</td>
        </tr>
      `).join('');
    }

    async function loadBotStats(refresh) {
      const url = refresh ? '/api/bot-stats?refresh=1' : '/api/bot-stats';
      try {
        const data = await json(url);
        renderBotStats(data);
      } catch (err) {
        const updated = document.querySelector('#bot-stats-updated');
        if (updated) updated.textContent = String(err);
      }
    }

    setInterval(() => {
      loadJobs();
      loadLog();
      loadBotStats(true);
    }, 1000);
    loadData().then(() => Promise.all([loadJobs(), loadBotStats(false)])).catch(err => { msgEl.textContent = String(err); });
  </script>
</body>
</html>
"""


REPLAYS_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Human Replay Upload</title>
  <style>
    :root { color-scheme: dark; --bg: #0f172a; --panel: #111827; --muted: #94a3b8; --text: #e5e7eb; --accent: #38bdf8; --ok: #22c55e; --bad: #ef4444; --warn: #f59e0b; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: linear-gradient(180deg, #020617, var(--bg)); color: var(--text); }
    header { padding: 24px 18px 8px; max-width: 1100px; margin: 0 auto; }
    h1 { margin: 0 0 6px; font-size: 28px; }
    h2 { margin: 0 0 10px; font-size: 20px; }
    p { color: var(--muted); line-height: 1.45; }
    main { display: grid; gap: 16px; padding: 16px; max-width: 1100px; margin: 0 auto; }
    section { background: rgba(17, 24, 39, .92); border: 1px solid rgba(148, 163, 184, .18); border-radius: 18px; padding: 16px; box-shadow: 0 18px 60px rgba(0,0,0,.25); }
    label { display: block; color: var(--muted); font-size: 13px; margin: 12px 0 6px; }
    select, input, button { width: 100%; border-radius: 12px; border: 1px solid rgba(148, 163, 184, .25); padding: 12px; background: #020617; color: var(--text); font-size: 16px; }
    button { margin-top: 14px; background: var(--accent); color: #082f49; border: 0; font-weight: 800; cursor: pointer; }
    button.secondary { background: #334155; color: var(--text); }
    button:disabled { opacity: .6; cursor: wait; }
    .grid { display: grid; gap: 16px; }
    .duel { border: 1px solid rgba(148, 163, 184, .16); border-radius: 14px; padding: 12px; margin-top: 10px; background: #020617; }
    .pill { display: inline-block; padding: 4px 9px; border-radius: 999px; font-size: 12px; font-weight: 700; background: #334155; margin-right: 6px; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 420px; overflow: auto; background: #020617; border-radius: 12px; padding: 12px; border: 1px solid rgba(148, 163, 184, .16); }
    .ok { color: var(--ok); }
    .bad { color: var(--bad); }
    .hint { font-size: 13px; margin-top: 8px; }
    a { color: var(--accent); }
    .stats-table { width:100%; border-collapse:collapse; font-size:13px; margin-top:8px; }
    .stats-table th, .stats-table td { text-align:left; padding:6px 8px; border-bottom:1px solid rgba(148,163,184,.14); }
    .stats-table th { color:var(--muted); font-size:11px; text-transform:uppercase; }
    @media (min-width: 850px) { .grid { grid-template-columns: 380px 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>Human Replay Upload</h1>
    <p>Upload EDOPro <code>.yrpX</code> replays or JSON duel logs — conversion, import, and learning happen here. <a href="/">← Training dashboard</a></p>
  </header>
  <main class="grid">
    <section>
      <h2>Upload replays</h2>
      <p class="hint">Pick any <code>.yrpX</code> from your EDOPro <code>replay/</code> folder (timestamped saves work too), or JSON duel logs. You can select multiple files.</p>
      <label for="files">Replay files</label>
      <input id="files" type="file" accept=".yrpX,.yrp,.json,application/json" multiple />
      <label for="study-agent">Your player name (for learning)</label>
      <input id="study-agent" type="text" placeholder="e.g. name shown in EDOPro duel" list="study-agent-list" />
      <datalist id="study-agent-list"></datalist>
      <label for="format">Format tag (optional)</label>
      <input id="format" type="text" placeholder="e.g. banlist-2026-01" list="format-list" />
      <datalist id="format-list"></datalist>
      <button id="upload">Upload, convert &amp; import</button>
      <button id="learn" class="secondary">Analyze &amp; update policy</button>
      <p id="message"></p>
    </section>
    <section>
      <h2>Imported replays</h2>
      <p id="stats">Loading catalog…</p>
      <div id="duels"></div>
    </section>
    <section style="grid-column: 1 / -1;">
      <h2>Bot training stats</h2>
      <p class="hint" id="bot-stats-updated">Loading…</p>
      <table class="stats-table">
        <thead>
          <tr>
            <th>Bot</th><th>Duels</th><th>Human</th><th>Sessions</th><th>Updates</th><th>Avg turns</th><th>Avg actions</th>
          </tr>
        </thead>
        <tbody id="bot-stats-body"><tr><td colspan="7">Loading…</td></tr></tbody>
      </table>
    </section>
    <section style="grid-column: 1 / -1;">
      <h2>What the bot learned</h2>
      <pre id="summary">Upload replays and click Analyze &amp; update policy.</pre>
    </section>
  </main>
  <script>
    const msgEl = document.querySelector('#message');
    const statsEl = document.querySelector('#stats');
    const duelsEl = document.querySelector('#duels');
    const summaryEl = document.querySelector('#summary');
    const studyInput = document.querySelector('#study-agent');
    const formatInput = document.querySelector('#format');

    async function json(url, options) {
      const res = await fetch(url, options);
      const text = await res.text();
      if (!res.ok) throw new Error(text || res.statusText);
      return text ? JSON.parse(text) : {};
    }

    function setMessage(text, ok) {
      msgEl.textContent = text;
      msgEl.className = ok ? 'ok' : 'bad';
    }

    function fillFilters(catalog) {
      const agents = catalog.study_agents || [];
      const formats = catalog.formats || [];
      const agentList = document.querySelector('#study-agent-list');
      const formatList = document.querySelector('#format-list');
      if (agentList) {
        agentList.innerHTML = agents.map(a => `<option value="${a}"></option>`).join('');
      }
      if (formatList) {
        formatList.innerHTML = formats.map(f => `<option value="${f}"></option>`).join('');
      }
    }

    function renderBotStats(data) {
      const updated = document.querySelector('#bot-stats-updated');
      const body = document.querySelector('#bot-stats-body');
      if (!updated || !body) return;
      updated.textContent = data.updated_at
        ? `Updated ${data.updated_at} (refreshes after each training session)`
        : 'No stats yet';
      const bots = data.bots || [];
      body.innerHTML = bots.length ? bots.map(bot => `
        <tr>
          <td>${bot.name} <span class="hint">(${bot.bot_id})</span></td>
          <td>${bot.training_duels ?? 0}</td>
          <td>${bot.human_duels ?? 0}</td>
          <td>${bot.training_sessions ?? 0}</td>
          <td>${bot.policy_updates ?? 0}</td>
          <td>${bot.avg_duel_turns ?? '—'}</td>
          <td>${bot.avg_decisions_per_game ?? '—'}</td>
        </tr>
      `).join('') : '<tr><td colspan="7">No training data yet.</td></tr>';
    }

    async function loadBotStats(refresh) {
      const url = refresh ? '/api/bot-stats?refresh=1' : '/api/bot-stats';
      try {
        renderBotStats(await json(url));
      } catch (err) {
        const el = document.querySelector('#bot-stats-updated');
        if (el) el.textContent = String(err);
      }
    }

    async function loadCatalog() {
      const catalog = await json('/api/human-duels');
      statsEl.textContent = `${catalog.duel_count} replays · ${catalog.total_decisions} decisions in catalog`;
      fillFilters(catalog);
      duelsEl.innerHTML = (catalog.duels || []).map(d => `
        <div class="duel">
          <strong>${d.duel_id}</strong><br/>
          <span class="pill">${d.format}</span>
          ${d.study_agent ? `<span class="pill">study: ${d.study_agent}</span>` : ''}
          <span class="pill">${d.decision_count} decisions</span>
          <p>${d.player_a || '?'} vs ${d.player_b || '?'}${d.winner ? ` · winner: ${d.winner}` : ''}</p>
        </div>
      `).join('') || '<p>No replays imported yet.</p>';
    }

    async function loadSummary() {
      const res = await fetch('/api/human-duels/summary');
      summaryEl.textContent = await res.text();
    }

    async function uploadFiles() {
      const input = document.querySelector('#files');
      const button = document.querySelector('#upload');
      if (!input.files.length) {
        setMessage('Choose at least one .json file.', false);
        return;
      }
      button.disabled = true;
      setMessage('Uploading and converting…', true);
      try {
        const form = new FormData();
        for (const file of input.files) form.append('files', file, file.name);
        if (studyInput.value.trim()) form.append('study_agent', studyInput.value.trim());
        if (formatInput.value.trim()) form.append('format', formatInput.value.trim());
        const res = await fetch('/api/human-duels/upload', { method: 'POST', body: form });
        const data = JSON.parse(await res.text());
        if (!res.ok) throw new Error((data.errors && data.errors[0] && data.errors[0].error) || 'Upload failed');
        const errCount = (data.errors || []).length;
        const converted = data.converted_from_replay || 0;
        let msg = `Imported ${data.imported} replay(s)`;
        if (converted) msg += ` (${converted} converted from EDOPro)`;
        if (errCount) msg += ` · ${errCount} error(s)`;
        setMessage(msg, errCount === 0 && data.imported > 0);
        input.value = '';
        await loadCatalog();
        if (data.bot_stats) renderBotStats(data.bot_stats);
        else await loadBotStats(true);
      } catch (err) {
        setMessage(String(err), false);
      } finally {
        button.disabled = false;
      }
    }

    async function learnFromCatalog() {
      const button = document.querySelector('#learn');
      button.disabled = true;
      setMessage('Analyzing replays and updating policy…', true);
      try {
        const payload = {
          study_agent: studyInput.value.trim() || null,
          format: formatInput.value.trim() || null,
        };
        const data = await json('/api/human-duels/learn', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        summaryEl.textContent = data.summary || 'Learning complete.';
        setMessage(`Learned from ${data.total_games} game(s), ${data.total_decisions} decision(s). Policy updated.`, true);
        if (data.bot_stats) renderBotStats(data.bot_stats);
        else await loadBotStats(true);
      } catch (err) {
        setMessage(String(err), false);
      } finally {
        button.disabled = false;
      }
    }

    document.querySelector('#upload').addEventListener('click', uploadFiles);
    document.querySelector('#learn').addEventListener('click', learnFromCatalog);
    loadCatalog().then(loadSummary).then(() => loadBotStats(false)).catch(err => setMessage(String(err), false));
    setInterval(() => loadBotStats(true), 5000);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
