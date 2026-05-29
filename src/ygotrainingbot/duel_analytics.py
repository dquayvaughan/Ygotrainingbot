"""Deck and decision analytics from the training database (Phase 5)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ygotrainingbot.training_db import bot_game_record, top_tags_for_bot


def deck_analytics(conn: sqlite3.Connection, bot_id: str) -> dict[str, Any]:
    overall = bot_game_record(conn, bot_id)
    first = bot_game_record(conn, bot_id, goes_first=True)
    second = bot_game_record(conn, bot_id, goes_first=False)
    passive_tags = {"phase", "decline"}
    tag_rows = top_tags_for_bot(conn, bot_id, limit=20)
    total_tag_hits = sum(count for _tag, count in tag_rows) or 1
    passive_hits = sum(count for tag, count in tag_rows if tag in passive_tags)
    return {
        "bot_id": bot_id,
        "overall": overall,
        "going_first": first,
        "going_second": second,
        "top_tags": tag_rows,
        "passive_tag_rate": passive_hits / total_tag_hits,
        "avg_decisions": overall["avg_decisions"],
    }


def opponent_breakdown(conn: sqlite3.Connection, bot_id: str) -> list[dict[str, Any]]:
    opponents = conn.execute(
        """
        SELECT DISTINCT CASE
            WHEN home_bot = ? THEN away_bot
            ELSE home_bot
        END AS opponent
        FROM games
        WHERE home_bot = ? OR away_bot = ?
        ORDER BY opponent
        """,
        (bot_id, bot_id, bot_id),
    ).fetchall()
    rows: list[dict[str, Any]] = []
    for opponent_row in opponents:
        opponent = str(opponent_row["opponent"])
        if not opponent or opponent == bot_id:
            continue
        record = bot_game_record(conn, bot_id, opponent_bot=opponent)
        name_row = conn.execute(
            """
            SELECT home_name, away_name, home_bot, away_bot
            FROM games
            WHERE (home_bot = ? AND away_bot = ?) OR (home_bot = ? AND away_bot = ?)
            LIMIT 1
            """,
            (bot_id, opponent, opponent, bot_id),
        ).fetchone()
        opponent_name = opponent
        if name_row is not None:
            if str(name_row["home_bot"]) == opponent:
                opponent_name = str(name_row["home_name"] or opponent)
            else:
                opponent_name = str(name_row["away_name"] or opponent)
        rows.append({"opponent_bot": opponent, "opponent_name": opponent_name, **record})
    rows.sort(key=lambda item: (item["games"], item["win_rate"]), reverse=True)
    return rows


def card_tag_contributions(conn: sqlite3.Connection, bot_id: str) -> list[dict[str, Any]]:
    """Approximate card-line contributions via winning-game tag rates."""

    wins = conn.execute(
        """
        SELECT g.id
        FROM games g
        WHERE g.winner = ?
        """,
        (bot_id,),
    ).fetchall()
    losses = conn.execute(
        """
        SELECT g.id
        FROM games g
        WHERE g.loser = ?
        """,
        (bot_id,),
    ).fetchall()
    win_ids = {int(row["id"]) for row in wins}
    loss_ids = {int(row["id"]) for row in losses}

    def tag_counter(game_ids: set[int]) -> dict[str, int]:
        counts: dict[str, int] = {}
        if not game_ids:
            return counts
        placeholders = ",".join("?" for _ in game_ids)
        rows = conn.execute(
            f"""
            SELECT tags FROM decisions
            WHERE agent = ? AND game_id IN ({placeholders})
            """,
            (bot_id, *sorted(game_ids)),
        ).fetchall()
        for row in rows:
            try:
                tags = json.loads(str(row["tags"] or "[]"))
            except json.JSONDecodeError:
                tags = []
            for tag in tags:
                counts[str(tag)] = counts.get(str(tag), 0) + 1
        return counts

    win_tags = tag_counter(win_ids)
    loss_tags = tag_counter(loss_ids)
    keys = sorted(set(win_tags) | set(loss_tags))
    contributions: list[dict[str, Any]] = []
    for tag in keys:
        win_hits = win_tags.get(tag, 0)
        loss_hits = loss_tags.get(tag, 0)
        total = win_hits + loss_hits
        if total <= 0:
            continue
        contributions.append(
            {
                "tag": tag,
                "wins_sample": win_hits,
                "losses_sample": loss_hits,
                "win_share": win_hits / total,
            }
        )
    contributions.sort(key=lambda item: (item["win_share"], item["wins_sample"]), reverse=True)
    return contributions[:20]


def analytics_to_learning_nudges(analytics: dict[str, Any]) -> dict[str, float]:
    """Convert analytics into small tag-weight nudges for policy learning."""

    nudges: dict[str, float] = {}
    for tag, _count in analytics.get("top_tags", []):
        nudges[str(tag)] = nudges.get(str(tag), 0.0) + 0.15
    if analytics.get("passive_tag_rate", 0.0) > 0.25:
        nudges["phase"] = nudges.get("phase", 0.0) - 0.5
        nudges["decline"] = nudges.get("decline", 0.0) - 0.5
        nudges["attack"] = nudges.get("attack", 0.0) + 0.35
    second = analytics.get("going_second", {})
    first = analytics.get("going_first", {})
    if second.get("games", 0) >= 3 and second.get("win_rate", 0.0) + 0.1 < first.get("win_rate", 0.0):
        nudges["removal"] = nudges.get("removal", 0.0) + 0.25
        nudges["battle-trap"] = nudges.get("battle-trap", 0.0) + 0.25
    return nudges
