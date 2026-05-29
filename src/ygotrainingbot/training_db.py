"""SQLite index over persisted duel game logs (Phase 4)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from ygotrainingbot.duel_logs import iter_game_log_paths_under


SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    year INTEGER,
    format_name TEXT,
    home_bot TEXT,
    away_bot TEXT,
    home_name TEXT,
    away_name TEXT,
    winner TEXT,
    loser TEXT,
    goes_first TEXT,
    end_reason TEXT,
    decisions INTEGER NOT NULL DEFAULT 0,
    runtime_errors INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    step INTEGER NOT NULL,
    turn INTEGER,
    agent TEXT,
    action_id TEXT,
    label TEXT,
    tags TEXT,
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_games_home ON games(home_bot);
CREATE INDEX IF NOT EXISTS idx_games_away ON games(away_bot);
CREATE INDEX IF NOT EXISTS idx_games_winner ON games(winner);
CREATE INDEX IF NOT EXISTS idx_games_goes_first ON games(goes_first);
CREATE INDEX IF NOT EXISTS idx_decisions_agent ON decisions(agent);
CREATE INDEX IF NOT EXISTS idx_decisions_game ON decisions(game_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _trace_fields(trace: dict[str, Any]) -> tuple[int | None, str | None, str | None, str | None, str]:
    state = trace.get("state")
    action = trace.get("action")
    if isinstance(state, dict) and isinstance(action, dict):
        turn = state.get("turn")
        agent = trace.get("agent_name", trace.get("agent"))
        action_id = action.get("action_id")
        label = action.get("label")
        tags = list(action.get("tags", ()))
    else:
        turn = trace.get("turn")
        agent = trace.get("agent", trace.get("agent_name"))
        action_id = trace.get("selected_action")
        label = trace.get("selected_label")
        tags = list(trace.get("selected_tags", ()))
    return (
        int(turn) if turn is not None else None,
        str(agent) if agent is not None else None,
        str(action_id) if action_id is not None else None,
        str(label) if label is not None else None,
        json.dumps(tags, sort_keys=True),
    )


def index_game_log(conn: sqlite3.Connection, path: Path) -> bool:
    """Index one game log. Returns True if newly indexed, False if unchanged path exists."""

    resolved = str(path.resolve())
    payload = json.loads(path.read_text(encoding="utf-8"))
    meta = dict(payload.get("meta", {}))
    result = dict(payload.get("result", {}))
    script_stats = dict(result.get("script_stats", {}))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    conn.execute("DELETE FROM games WHERE path = ?", (resolved,))
    cursor = conn.execute(
        """
        INSERT INTO games (
            path, year, format_name, home_bot, away_bot, home_name, away_name,
            winner, loser, goes_first, end_reason, decisions, runtime_errors, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resolved,
            int(meta.get("year", 0) or 0),
            str(meta.get("format", meta.get("format_name", "")) or ""),
            str(meta.get("home_bot_id", "")),
            str(meta.get("away_bot_id", "")),
            str(meta.get("home_name", "")),
            str(meta.get("away_name", "")),
            str(result.get("winner") or ""),
            str(result.get("loser") or ""),
            str(meta.get("goes_first") or ""),
            str(result.get("end_reason") or ""),
            int(result.get("decisions", len(payload.get("traces", []))) or 0),
            int(script_stats.get("runtime_errors", 0) or 0),
            now,
        ),
    )
    game_id = int(cursor.lastrowid)
    decision_rows = []
    for step, trace in enumerate(payload.get("traces", []), start=1):
        if not isinstance(trace, dict):
            continue
        turn, agent, action_id, label, tags_json = _trace_fields(trace)
        decision_rows.append((game_id, step, turn, agent, action_id, label, tags_json))
    if decision_rows:
        conn.executemany(
            """
            INSERT INTO decisions (game_id, step, turn, agent, action_id, label, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            decision_rows,
        )
    return True


def index_roots(conn: sqlite3.Connection, roots: Iterable[Path]) -> dict[str, int]:
    indexed = 0
    skipped = 0
    for root in roots:
        if not root.is_dir():
            skipped += 1
            continue
        for path in iter_game_log_paths_under(root):
            try:
                index_game_log(conn, path)
                indexed += 1
            except (OSError, json.JSONDecodeError, ValueError):
                skipped += 1
    conn.commit()
    return {"indexed": indexed, "skipped": skipped}


def bot_game_record(
    conn: sqlite3.Connection,
    bot_id: str,
    *,
    goes_first: bool | None = None,
    opponent_bot: str | None = None,
) -> dict[str, Any]:
    clauses = ["(home_bot = ? OR away_bot = ?)"]
    params: list[Any] = [bot_id, bot_id]
    if goes_first is True:
        clauses.append("goes_first = ?")
        params.append(bot_id)
    elif goes_first is False:
        clauses.append("goes_first != ?")
        params.append(bot_id)
    if opponent_bot:
        clauses.append("((home_bot = ? AND away_bot = ?) OR (home_bot = ? AND away_bot = ?))")
        params.extend([bot_id, opponent_bot, opponent_bot, bot_id])
    where = " AND ".join(clauses)
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS games,
            SUM(CASE WHEN winner = ? THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN winner != ? AND winner != '' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN winner = '' OR winner IS NULL THEN 1 ELSE 0 END) AS draws,
            AVG(decisions) AS avg_decisions
        FROM games
        WHERE {where}
        """,
        [bot_id, bot_id, *params],
    ).fetchone()
    games = int(row["games"] or 0)
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    decisive = wins + losses
    return {
        "bot_id": bot_id,
        "games": games,
        "wins": wins,
        "losses": losses,
        "draws": int(row["draws"] or 0),
        "win_rate": wins / decisive if decisive else 0.0,
        "avg_decisions": float(row["avg_decisions"] or 0.0),
        "goes_first": goes_first,
        "opponent_bot": opponent_bot,
    }


def top_tags_for_bot(conn: sqlite3.Connection, bot_id: str, *, limit: int = 12) -> list[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT d.tags, COUNT(*) AS count
        FROM decisions d
        JOIN games g ON g.id = d.game_id
        WHERE d.agent = ?
        GROUP BY d.tags
        ORDER BY count DESC
        LIMIT ?
        """,
        (bot_id, limit),
    ).fetchall()
    tag_counts: dict[str, int] = {}
    for row in rows:
        try:
            tags = json.loads(str(row["tags"] or "[]"))
        except json.JSONDecodeError:
            tags = []
        for tag in tags:
            tag_counts[str(tag)] = tag_counts.get(str(tag), 0) + int(row["count"])
    return sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[:limit]


def query_games(
    conn: sqlite3.Connection,
    *,
    bot_id: str | None = None,
    opponent_bot: str | None = None,
    winner: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if bot_id:
        clauses.append("(home_bot = ? OR away_bot = ?)")
        params.extend([bot_id, bot_id])
    if opponent_bot and bot_id:
        clauses.append("((home_bot = ? AND away_bot = ?) OR (home_bot = ? AND away_bot = ?))")
        params.extend([bot_id, opponent_bot, opponent_bot, bot_id])
    if winner:
        clauses.append("winner = ?")
        params.append(winner)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT path, year, home_name, away_name, home_bot, away_bot, winner, goes_first, end_reason, decisions
        FROM games
        {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    return [dict(row) for row in rows]


def database_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    games = int(conn.execute("SELECT COUNT(*) FROM games").fetchone()[0])
    decisions = int(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])
    bots = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT bot FROM (
                SELECT home_bot AS bot FROM games
                UNION
                SELECT away_bot AS bot FROM games
            ) ORDER BY bot
            """
        ).fetchall()
        if row[0]
    ]
    return {"games": games, "decisions": decisions, "bots": bots}
