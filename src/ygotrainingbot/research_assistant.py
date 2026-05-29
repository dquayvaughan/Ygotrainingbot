"""Plain-English research assistant over training DB + progress files (Phase 8)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ygotrainingbot.duel_analytics import deck_analytics, opponent_breakdown
from ygotrainingbot.training_db import bot_game_record, connect, database_summary, query_games


def _detect_bot_id(question: str, default: str = "bot-01") -> str:
    match = re.search(r"\bbot-\d{2}\b", question, flags=re.IGNORECASE)
    if match:
        return match.group(0).lower()
    if "yugi" in question.lower():
        return "bot-01"
    return default


def _load_progress(progress_dir: Path | None, bot_id: str) -> dict[str, Any]:
    if progress_dir is None:
        return {}
    for name in ("training-loop-progress.json", "protagonist-progress.json"):
        path = progress_dir / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if name.startswith("training-loop"):
            latest = payload.get("latest")
            if isinstance(latest, dict):
                return latest
        latest = payload.get("latest")
        if isinstance(latest, dict):
            return latest
    return {}


def answer_training_question(
    db_path: Path,
    question: str,
    *,
    default_bot_id: str = "bot-01",
    progress_dir: Path | None = None,
) -> str:
    conn = connect(db_path)
    bot_id = _detect_bot_id(question, default_bot_id)
    q = question.lower().strip()
    summary = database_summary(conn)

    if not summary["games"]:
        return (
            "I do not have any indexed games yet. "
            "Run `index-training-db` on a bracket folder such as data/yearly-bracket-watch first."
        )

    if any(token in q for token in ("summary", "database", "how many games")):
        return (
            f"Training database contains {summary['games']} games, "
            f"{summary['decisions']} decisions, and {len(summary['bots'])} bots."
        )

    if "opponent" in q or "matchup" in q or "vs" in q:
        rows = opponent_breakdown(conn, bot_id)
        if not rows:
            return f"No opponent breakdown found for {bot_id}."
        lines = [f"Matchup breakdown for {bot_id}:"]
        for row in rows[:8]:
            lines.append(
                f"- vs {row['opponent_name']} ({row['opponent_bot']}): "
                f"{row['wins']}-{row['losses']} ({row['win_rate']:.1%} decisive win rate over {row['games']} games)"
            )
        return "\n".join(lines)

    if "going second" in q or "go second" in q or "second" in q:
        record = bot_game_record(conn, bot_id, goes_first=False)
        return (
            f"{bot_id} going second: {record['wins']}-{record['losses']} "
            f"({record['win_rate']:.1%} decisive win rate over {record['games']} games)."
        )

    if "going first" in q or "go first" in q or "first" in q:
        record = bot_game_record(conn, bot_id, goes_first=True)
        return (
            f"{bot_id} going first: {record['wins']}-{record['losses']} "
            f"({record['win_rate']:.1%} decisive win rate over {record['games']} games)."
        )

    if "loss" in q and ("recent" in q or "latest" in q):
        games = query_games(conn, bot_id=bot_id, winner="", limit=5)
        filtered = [game for game in games if game.get("winner") and game.get("winner") != bot_id]
        if not filtered:
            filtered = query_games(conn, bot_id=bot_id, limit=5)
        lines = [f"Recent games for {bot_id}:"]
        for game in filtered[:5]:
            lines.append(
                f"- {game['home_name']} vs {game['away_name']}: winner={game['winner'] or 'draw'} "
                f"({game['end_reason']}, {game['decisions']} decisions)"
            )
        return "\n".join(lines)

    if "analytics" in q or "deck" in q or "tag" in q:
        analytics = deck_analytics(conn, bot_id)
        lines = [
            f"Deck analytics for {bot_id}:",
            f"- Overall: {analytics['overall']['wins']}-{analytics['overall']['losses']} "
            f"({analytics['overall']['win_rate']:.1%})",
            f"- Going first: {analytics['going_first']['win_rate']:.1%}",
            f"- Going second: {analytics['going_second']['win_rate']:.1%}",
            f"- Passive tag rate: {analytics['passive_tag_rate']:.1%}",
            "- Top tags: "
            + ", ".join(f"{tag}={count}" for tag, count in analytics["top_tags"][:6]),
        ]
        return "\n".join(lines)

    progress = _load_progress(progress_dir, bot_id)
    if progress:
        return (
            f"Latest progress for {bot_id}: "
            f"series win rate {float(progress.get('series_win_rate', 0.0)):.1%}, "
            f"decisive game win rate {float(progress.get('game_decisive_win_rate', 0.0)):.1%}, "
            f"policy observations {int(progress.get('policy_observations', 0))}."
        )

    record = bot_game_record(conn, bot_id)
    return (
        f"{bot_id} overall: {record['wins']}-{record['losses']} "
        f"({record['win_rate']:.1%} decisive win rate over {record['games']} indexed games)."
    )
