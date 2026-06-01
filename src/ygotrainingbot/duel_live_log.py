"""Human-readable, line-buffered duel progress for training jobs."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(slots=True)
class DuelLiveContext:
    """Labels for the duel currently being played."""

    matchup: str = ""
    game: int = 0
    games_total: int = 0
    deck_a: str = ""
    deck_b: str = ""


_active_context: DuelLiveContext | None = None


def set_duel_live_context(**kwargs: object) -> None:
    global _active_context
    _active_context = DuelLiveContext(
        matchup=str(kwargs.get("matchup", "") or ""),
        game=int(kwargs.get("game", 0) or 0),
        games_total=int(kwargs.get("games_total", 0) or 0),
        deck_a=str(kwargs.get("deck_a", "") or ""),
        deck_b=str(kwargs.get("deck_b", "") or ""),
    )


def clear_duel_live_context() -> None:
    global _active_context
    _active_context = None


def duel_live_log_from_env() -> DuelLiveLog | None:
    path = os.environ.get("YGOTRAIN_DUEL_LIVE_LOG", "").strip()
    if not path:
        return None
    return DuelLiveLog(Path(path))


class DuelLiveLog:
    """Append timestamped duel events to a file and stdout (for dashboard tailing)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}\n"
        _write_stdout_line(line)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()

    def section(self, title: str) -> None:
        self.emit(f"--- {title} ---")

    def matchup_start(self, deck_a: str, deck_b: str, games: int) -> None:
        set_duel_live_context(matchup=f"{deck_a} vs {deck_b}", deck_a=deck_a, deck_b=deck_b, games_total=games)
        self.section(f"Matchup: {deck_a} vs {deck_b} ({games} game(s))")

    def matchup_done(self, *, wins: Mapping[str, int], failed: int, draws: int) -> None:
        ctx = _active_context
        label = ctx.matchup if ctx else "matchup"
        win_bits = ", ".join(f"{name}={count}" for name, count in sorted(wins.items())) or "none"
        self.emit(
            f"Matchup done {label}: wins [{win_bits}], failed={failed}, draws={draws}",
        )

    def duel_start(
        self,
        *,
        game: int,
        games_total: int,
        first_agent: str,
        second_agent: str,
        policy_a: str,
        policy_b: str,
        seed: tuple[int, ...] | list[int],
    ) -> None:
        ctx = _active_context
        matchup = ctx.matchup if ctx else "duel"
        if ctx:
            set_duel_live_context(
                matchup=ctx.matchup,
                deck_a=ctx.deck_a,
                deck_b=ctx.deck_b,
                game=game,
                games_total=games_total,
            )
        self.emit(
            f">> Duel {game}/{games_total} {matchup} | "
            f"{first_agent}({policy_a}) vs {second_agent}({policy_b}) | seed={list(seed)}",
        )

    def duel_decision(
        self,
        *,
        decision_index: int,
        agent: str,
        action_id: str,
        label: str,
        summary: str,
    ) -> None:
        # Keep the feed readable during long combo lines.
        if decision_index > 1 and decision_index % 20 != 0:
            return
        ctx = _active_context
        prefix = ""
        if ctx and ctx.game:
            prefix = f"G{ctx.game} "
        short_label = _shorten(label, 72)
        lp = _life_points_from_summary(summary)
        lp_bit = f" | {lp}" if lp else ""
        self.emit(
            f"  {prefix}#{decision_index} {agent} -> {action_id}"
            f"{f' ({short_label})' if short_label else ''}{lp_bit}",
        )

    def duel_end(self, report: Mapping[str, Any]) -> None:
        ctx = _active_context
        game = int(report.get("game_number") or (ctx.game if ctx else 0) or 0)
        end_reason = str(report.get("end_reason", "unknown"))
        decisions = int(report.get("traced_decisions", 0) or 0)
        life_points = report.get("life_points")
        lp_text = ""
        if isinstance(life_points, (list, tuple)) and len(life_points) == 2:
            lp_text = f" LP {life_points[0]}-{life_points[1]}"
        wins = dict(report.get("wins_by_agent") or {})
        if wins:
            winner = next(iter(wins.keys()))
            outcome = f"winner={winner}"
        elif int(report.get("draws", 0) or 0):
            outcome = "draw"
        elif end_reason in {"retry_stuck", "engine_stall", "max_decisions"}:
            outcome = f"sim_fault ({end_reason})"
        else:
            outcome = end_reason
        prefix = f"G{game} " if game else ""
        self.emit(f"OK {prefix}done: {outcome}, {decisions} decisions{lp_text}")

    def duel_fail(self, *, game: int, error: str) -> None:
        ctx = _active_context
        matchup = ctx.matchup if ctx else "duel"
        self.emit(f"FAIL G{game} {matchup}: {_shorten(error, 200)}")


def _write_stdout_line(line: str) -> None:
    """Write to stdout without crashing on Windows cp1252 consoles."""

    try:
        sys.stdout.buffer.write(line.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
        return
    except (OSError, AttributeError):
        pass
    try:
        sys.stdout.write(line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8",
            errors="replace",
        ))
        sys.stdout.flush()
    except (OSError, UnicodeEncodeError):
        pass


def _shorten(text: str, max_len: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _life_points_from_summary(summary: str) -> str:
    if "LP " not in summary:
        return ""
    start = summary.find("LP ")
    fragment = summary[start : start + 80]
    return fragment.strip().rstrip("|").strip()
