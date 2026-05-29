"""Year-by-year league bracket tournaments (best-of-3 series, per-bot learning)."""

from __future__ import annotations

import json
import random
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from ygotrainingbot.duel_logs import collect_game_log_paths, game_log_path_for_series
from ygotrainingbot.format_training import FormatDeck, load_format_pack

START_YEAR = 2010
END_YEAR = 2025
# Protagonist bot (Yugi); CLI flag remains --ethan-bot-id for compatibility.
ETHAN_BOT_ID = "bot-01"
PROTAGONIST_BOT_ID = ETHAN_BOT_ID
GOAT_PACK = "configs/format-packs/goat-2005.json"
EDISON_PACK = "configs/format-packs/edison-2010.json"

TIER23_BOTS = frozenset({"bot-01", "bot-02", "bot-03"})

# Archetype labels from the Edison→present assignment table (stored for reporting).
YEARLY_ARCHETYPE_BY_BOT: dict[int, dict[str, str]] = {
    2010: {
        "bot-01": "Frog Monarch",
        "bot-02": "Machina Gadget",
        "bot-03": "Gravekeeper",
        "bot-04": "Quickdraw Dandywarrior",
        "bot-05": "X-Saber",
        "bot-06": "Infernity",
        "bot-07": "Plant Synchro",
        "bot-08": "Blackwing",
        "bot-09": "Quickdraw Dandywarrior",
    },
    2011: {
        "bot-01": "Gravekeeper",
        "bot-02": "T.G. Stun",
        "bot-03": "HERO Beat",
        "bot-04": "Agents",
        "bot-05": "Plant Synchro",
        "bot-06": "Six Samurai",
        "bot-07": "Dark World",
        "bot-08": "Karakuri",
        "bot-09": "Six Samurai",
    },
    2012: {
        "bot-01": "Geargia",
        "bot-02": "Agents",
        "bot-03": "HERO Beat",
        "bot-04": "Wind-Up",
        "bot-05": "Dino Rabbit",
        "bot-06": "Inzektor",
        "bot-07": "Chaos Dragon",
        "bot-08": "Mermail",
        "bot-09": "Wind-Up",
    },
    2013: {
        "bot-01": "Bujin",
        "bot-02": "Fire Fist",
        "bot-03": "Evilswarm",
        "bot-04": "Spellbook",
        "bot-05": "Mermail",
        "bot-06": "Dragon Ruler",
        "bot-07": "Geargia",
        "bot-08": "Fire Fist",
        "bot-09": "Spellbook",
    },
    2014: {
        "bot-01": "Bujin",
        "bot-02": "Geargia",
        "bot-03": "Fire Fist",
        "bot-04": "Shaddoll",
        "bot-05": "Burning Abyss",
        "bot-06": "HAT",
        "bot-07": "Satellarknight",
        "bot-08": "Shaddoll",
        "bot-09": "HAT",
    },
    2015: {
        "bot-01": "Ritual Beast",
        "bot-02": "HERO",
        "bot-03": "Kozmo",
        "bot-04": "Nekroz",
        "bot-05": "Burning Abyss",
        "bot-06": "Qliphort",
        "bot-07": "Shaddoll",
        "bot-08": "Nekroz",
        "bot-09": "Burning Abyss",
    },
    2016: {
        "bot-01": "Infernoid",
        "bot-02": "ABC",
        "bot-03": "Mermail Atlantean",
        "bot-04": "Monarch",
        "bot-05": "Pendulum Magician",
        "bot-06": "Kozmo",
        "bot-07": "Blue-Eyes",
        "bot-08": "Phantom Knights",
        "bot-09": "ABC",
    },
    2017: {
        "bot-01": "Paleozoic",
        "bot-02": "Invoked",
        "bot-03": "Dinosaur",
        "bot-04": "True Draco",
        "bot-05": "Pendulum Magician",
        "bot-06": "SPYRAL",
        "bot-07": "Zoodiac",
        "bot-08": "True Draco",
        "bot-09": "Trickstar",
    },
    2018: {
        "bot-01": "Altergeist",
        "bot-02": "Trickstar",
        "bot-03": "Dinosaur",
        "bot-04": "Sky Striker",
        "bot-05": "Thunder Dragon",
        "bot-06": "Gouki",
        "bot-07": "Orcust",
        "bot-08": "Sky Striker",
        "bot-09": "Thunder Dragon",
    },
    2019: {
        "bot-01": "Altergeist",
        "bot-02": "Salamangreat",
        "bot-03": "True Draco",
        "bot-04": "Sky Striker",
        "bot-05": "Orcust",
        "bot-06": "Danger Thunder",
        "bot-07": "Thunder Dragon",
        "bot-08": "Orcust",
        "bot-09": "Salamangreat",
    },
    2020: {
        "bot-01": "Altergeist",
        "bot-02": "Salamangreat",
        "bot-03": "Dinosaur",
        "bot-04": "Eldlich",
        "bot-05": "Dragon Link",
        "bot-06": "Adamancipator",
        "bot-07": "Invoked Dogmatika",
        "bot-08": "Eldlich",
        "bot-09": "Dragon Link",
    },
    2021: {
        "bot-01": "Eldlich",
        "bot-02": "Prank-Kids",
        "bot-03": "Phantom Knights",
        "bot-04": "Swordsoul",
        "bot-05": "Tri-Brigade",
        "bot-06": "Drytron",
        "bot-07": "Invoked Dogmatika Shaddoll",
        "bot-08": "Tri-Brigade",
        "bot-09": "Swordsoul",
    },
    2022: {
        "bot-01": "Labrynth",
        "bot-02": "Mathmech",
        "bot-03": "Floowandereeze",
        "bot-04": "Branded Despia",
        "bot-05": "Swordsoul Tenyi",
        "bot-06": "Tearlaments",
        "bot-07": "Runick Control",
        "bot-08": "Spright",
        "bot-09": "Branded Despia",
    },
    2023: {
        "bot-01": "Labrynth",
        "bot-02": "Purrely",
        "bot-03": "Mikanko",
        "bot-04": "Unchained",
        "bot-05": "Kashtira",
        "bot-06": "Tearlaments",
        "bot-07": "Branded Despia",
        "bot-08": "Rescue-ACE",
        "bot-09": "Spright",
    },
    2024: {
        "bot-01": "Labrynth",
        "bot-02": "Voiceless Voice",
        "bot-03": "Tenpai Dragon",
        "bot-04": "Yubel Fiendsmith",
        "bot-05": "Snake-Eye",
        "bot-06": "Fire King Snake-Eye",
        "bot-07": "Tearlaments Horus",
        "bot-08": "Ryzeal Fiendsmith",
        "bot-09": "Snake-Eye",
    },
    2025: {
        "bot-01": "Vanquish Soul",
        "bot-02": "Memento",
        "bot-03": "Fire King",
        "bot-04": "Dracotail Branded",
        "bot-05": "Ryzeal",
        "bot-06": "Fiendsmith Ryzeal",
        "bot-07": "Yummy Control",
        "bot-08": "Ryzeal",
        "bot-09": "Yummy",
    },
}


@dataclass(frozen=True, slots=True)
class BotSeasonState:
    bot_id: str
    name: str
    policy: str
    characteristics: str
    policy_path: Path
    archetype: str
    pack_path: Path
    deck: FormatDeck


@dataclass(frozen=True, slots=True)
class Bo3SeriesResult:
    home_bot: str
    away_bot: str
    series_winner: str | None
    home_series_wins: int
    away_series_wins: int
    games_played: int
    game_reports: tuple[dict[str, Any], ...]


PlayDuelFn = Callable[..., dict[str, Any]]
LearnFn = Callable[..., tuple[dict[str, Any], str]]
CombineWeightsFn = Callable[..., dict[str, float]]
WritePolicyFn = Callable[..., None]
LoadPolicyWeightsFn = Callable[[Path | None], dict[str, float] | None]
MaterializePackFn = Callable[..., Path]


GOAT_CONTROL = "Goat Control representative top shell"
CHAOS_WARRIOR = "Chaos Warrior representative top shell"
QUICKDRAW = "Quickdraw Dandywarrior representative top shell"
FROG_MONARCH = "Frog Monarch representative top shell"


def resolve_year_deck(bot_id: str, year: int, profile: dict[str, object]) -> tuple[Path, FormatDeck, str]:
    """Map yearly archetype label to a concrete shell from goat/edison packs."""

    yearly = profile.get("yearly_decks")
    archetype = YEARLY_ARCHETYPE_BY_BOT.get(year, {}).get(bot_id, "unknown")
    if isinstance(yearly, dict):
        year_entry = yearly.get(str(year))
        if isinstance(year_entry, dict):
            archetype = str(year_entry.get("archetype", archetype))

    pack_path, deck_name = _archetype_to_pack_and_deck(archetype, tier23=bot_id in TIER23_BOTS)
    pack = load_format_pack(pack_path)
    selected = next((deck for deck in pack.decks if deck.name == deck_name), pack.decks[0])
    return pack_path, selected, archetype


def bot_states_for_year(
    profiles: Sequence[dict[str, object]],
    output_dir: Path,
    year: int,
) -> list[BotSeasonState]:
    """Rebuild season bot states from roster profiles and on-disk policies."""

    bots: list[BotSeasonState] = []
    for profile in profiles:
        bot_id = str(profile["bot_id"])
        policy_path = output_dir / "bots" / bot_id / "policy.json"
        pack_path, deck, archetype = resolve_year_deck(bot_id, year, profile)
        bots.append(
            BotSeasonState(
                bot_id=bot_id,
                name=str(profile.get("name", bot_id)),
                policy=str(profile["policy"]),
                characteristics=str(profile.get("characteristics", "")),
                policy_path=policy_path,
                archetype=archetype,
                pack_path=pack_path,
                deck=deck,
            )
        )
    return bots


def _archetype_to_pack_and_deck(archetype: str, *, tier23: bool) -> tuple[Path, str]:
    label = archetype.lower()
    edison_aggressive = (
        "quickdraw",
        "blackwing",
        "x-saber",
        "infernity",
        "wind-up",
        "six samurai",
        "nekroz",
        "sky striker",
        "agent",
        "spellbook",
        "dragon ruler",
        "shaddoll",
        "hat",
        "qliphort",
        "spyral",
        "orcust",
        "salamangreat",
        "tearlament",
        "spright",
        "kashtira",
        "snake",
        "ryzeal",
        "branded",
        "drytron",
        "tri-brigade",
        "swordsoul",
        "unchained",
        "rescue",
        "pendulum",
        "zoodiac",
        "true draco",
        "gouki",
        "thunder",
        "danger",
        "adamancipator",
        "mathmech",
        "voiceless",
        "tenpai",
        "dracotail",
        "fiendsmith",
        "yubel",
    )
    edison_control = ("frog", "monarch", "plant", "geargia", "ritual", "mermail", "fire fist", "bujin")
    goat_style = ("gravekeeper", "machina", "chaos", "dark world", "hero", "grave", "goat", "infernoid", "paleozoic")

    if any(token in label for token in edison_aggressive):
        return Path(EDISON_PACK), QUICKDRAW
    if any(token in label for token in edison_control):
        return Path(EDISON_PACK), FROG_MONARCH
    if any(token in label for token in goat_style):
        if "chaos" in label or "grave" in label or "hero" in label or "machina" in label:
            return Path(GOAT_PACK), CHAOS_WARRIOR
        return Path(GOAT_PACK), GOAT_CONTROL
    if any(token in label for token in ("altergeist", "eldlich", "labrynth", "purrely", "mikanko", "memento", "vanquish", "yummy")):
        if tier23:
            return Path(GOAT_PACK), GOAT_CONTROL
        return Path(EDISON_PACK), FROG_MONARCH
    if tier23:
        return Path(EDISON_PACK), FROG_MONARCH
    return Path(EDISON_PACK), QUICKDRAW


def generate_duel_seed(rng: random.Random) -> tuple[int, int, int, int]:
    """Return four u64 seeds for ocgcore shuffle and draw randomness."""

    return tuple(rng.getrandbits(64) for _ in range(4))


def pick_first_player_for_game(
    game_number: int,
    *,
    home_bot_id: str,
    away_bot_id: str,
    previous_game_loser: str | None,
    rng: random.Random,
) -> str:
    """Apply YGO Bo3 rules for who goes first (player 0 / deck A)."""

    if game_number == 1 or previous_game_loser is None:
        return rng.choice([home_bot_id, away_bot_id])
    if rng.random() < 0.5:
        return previous_game_loser
    return away_bot_id if previous_game_loser == home_bot_id else home_bot_id


def merge_training_reports(
    reports: Sequence[dict[str, Any]],
    *,
    format_name: str,
    bot_agent: str | None = None,
) -> dict[str, Any]:
    wins_by_agent: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    game_log_paths: list[str] = []
    seen_log_paths: set[str] = set()
    total_decisions = 0
    draws = 0
    games = 0

    for report in reports:
        games += int(report.get("games", 0))
        draws += int(report.get("draws", 0))
        total_decisions += int(report.get("traced_decisions", 0))
        wins_by_agent.update({str(k): int(v) for k, v in dict(report.get("wins_by_agent", {})).items()})
        tags.update({str(k): int(v) for k, v in dict(report.get("tags", {})).items()})
        action_counts.update({str(k): int(v) for k, v in dict(report.get("action_counts", {})).items()})
        for path in collect_game_log_paths(report):
            path_text = str(path)
            if path_text not in seen_log_paths:
                seen_log_paths.add(path_text)
                game_log_paths.append(path_text)

    merged: dict[str, Any] = {
        "format": format_name,
        "games": games,
        "draws": draws,
        "traced_decisions": total_decisions,
        "wins_by_agent": dict(wins_by_agent),
        "tags": dict(tags),
        "action_counts": dict(action_counts),
        "game_log_paths": game_log_paths,
        "decision_samples": [],
        "matchups": [{"report": report} for report in reports],
    }
    if bot_agent:
        merged["bot_agent"] = bot_agent
    return merged


def filter_report_for_bot(report: dict[str, Any], bot_agent: str) -> dict[str, Any]:
    """Keep only this bot's decisions and wins for isolated post-season learning."""

    from ygotrainingbot.duel_logs import load_decision_samples_for_learning

    scoped = dict(report)
    scoped["bot_agent"] = bot_agent
    samples = load_decision_samples_for_learning(scoped)

    tags: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    for sample in samples:
        tags.update(str(tag) for tag in sample.get("selected_tags", ()))
        action = sample.get("selected_action")
        if action:
            actions[str(action)] += 1

    wins = dict(report.get("wins_by_agent", {}))
    bot_wins = int(wins.get(bot_agent, 0))
    try:
        games = max(1, int(report.get("games", 1)))
    except (TypeError, ValueError):
        games = 1
    try:
        draws = max(0, int(report.get("draws", 0)))
    except (TypeError, ValueError):
        draws = 0

    filtered: dict[str, Any] = {
        "format": report.get("format"),
        "games": games,
        "draws": draws,
        "traced_decisions": len(samples),
        "wins_by_agent": {bot_agent: bot_wins} if bot_wins else {},
        "tags": dict(tags),
        "action_counts": dict(actions),
        "decision_samples": samples,
        "bot_agent": bot_agent,
    }
    game_log_path = report.get("game_log_path")
    if game_log_path:
        filtered["game_log_path"] = game_log_path
    return filtered


def play_bo3_series(
    *,
    play_duel: PlayDuelFn,
    gateway_command: str,
    home: BotSeasonState,
    away: BotSeasonState,
    timeout_seconds: float,
    format_name: str,
    rng: random.Random,
    max_games: int = 9,
    games_dir: Path | None = None,
    year: int | None = None,
    series_index: int = 0,
) -> Bo3SeriesResult:
    home_wins = 0
    away_wins = 0
    game_reports: list[dict[str, Any]] = []
    games_played = 0
    previous_game_loser: str | None = None

    while home_wins < 2 and away_wins < 2 and games_played < max_games:
        games_played += 1
        first_bot_id = pick_first_player_for_game(
            games_played,
            home_bot_id=home.bot_id,
            away_bot_id=away.bot_id,
            previous_game_loser=previous_game_loser,
            rng=rng,
        )
        second_bot_id = away.bot_id if first_bot_id == home.bot_id else home.bot_id
        first_bot = home if first_bot_id == home.bot_id else away
        second_bot = away if first_bot_id == home.bot_id else home
        seed = generate_duel_seed(rng)
        game_log_path = None
        if games_dir is not None and year is not None:
            game_log_path = game_log_path_for_series(
                games_dir,
                year=year,
                home_bot_id=home.bot_id,
                away_bot_id=away.bot_id,
                series_index=series_index,
                game_number=games_played,
            )

        report = play_duel(
            gateway_command,
            first_agent=first_bot.bot_id,
            second_agent=second_bot.bot_id,
            first_policy=first_bot.policy,
            second_policy=second_bot.policy,
            first_weights=first_bot.policy_path,
            second_weights=second_bot.policy_path,
            first_deck=first_bot.deck,
            second_deck=second_bot.deck,
            seed=seed,
            timeout_seconds=timeout_seconds,
            format_name=format_name,
            game_number=games_played,
            goes_first=first_bot_id,
            game_log_path=game_log_path,
            game_meta={
                "year": year,
                "series_index": series_index,
                "game_number": games_played,
                "home_bot_id": home.bot_id,
                "home_name": home.name,
                "away_bot_id": away.bot_id,
                "away_name": away.name,
                "home_archetype": home.archetype,
                "away_archetype": away.archetype,
                "home_deck": home.deck.name,
                "away_deck": away.deck.name,
                "goes_first": first_bot_id,
                "duel_seed": list(seed),
            },
        )
        game_reports.append(report)
        wins = dict(report.get("wins_by_agent", {}))
        if int(wins.get(home.bot_id, 0)) > 0:
            home_wins += 1
            previous_game_loser = away.bot_id
        elif int(wins.get(away.bot_id, 0)) > 0:
            away_wins += 1
            previous_game_loser = home.bot_id

    if home_wins > away_wins:
        series_winner = home.bot_id
    elif away_wins > home_wins:
        series_winner = away.bot_id
    elif home_wins == away_wins and home_wins > 0:
        series_winner = home.bot_id if rng.random() < 0.5 else away.bot_id
    else:
        series_winner = None

    return Bo3SeriesResult(
        home_bot=home.bot_id,
        away_bot=away.bot_id,
        series_winner=series_winner,
        home_series_wins=home_wins,
        away_series_wins=away_wins,
        games_played=games_played,
        game_reports=tuple(game_reports),
    )


def play_fixed_games_matchup(
    *,
    play_duel: PlayDuelFn,
    gateway_command: str,
    home: BotSeasonState,
    away: BotSeasonState,
    timeout_seconds: float,
    format_name: str,
    rng: random.Random,
    games_per_matchup: int,
    games_dir: Path | None = None,
    year: int | None = None,
    series_index: int = 0,
) -> Bo3SeriesResult:
    home_wins = 0
    away_wins = 0
    game_reports: list[dict[str, Any]] = []
    previous_game_loser: str | None = None

    for game_number in range(1, games_per_matchup + 1):
        first_bot_id = pick_first_player_for_game(
            game_number,
            home_bot_id=home.bot_id,
            away_bot_id=away.bot_id,
            previous_game_loser=previous_game_loser,
            rng=rng,
        )
        second_bot_id = away.bot_id if first_bot_id == home.bot_id else home.bot_id
        first_bot = home if first_bot_id == home.bot_id else away
        second_bot = away if first_bot_id == home.bot_id else home
        seed = generate_duel_seed(rng)
        game_log_path = None
        if games_dir is not None and year is not None:
            game_log_path = game_log_path_for_series(
                games_dir,
                year=year,
                home_bot_id=home.bot_id,
                away_bot_id=away.bot_id,
                series_index=series_index,
                game_number=game_number,
            )

        report = play_duel(
            gateway_command,
            first_agent=first_bot.bot_id,
            second_agent=second_bot.bot_id,
            first_policy=first_bot.policy,
            second_policy=second_bot.policy,
            first_weights=first_bot.policy_path,
            second_weights=second_bot.policy_path,
            first_deck=first_bot.deck,
            second_deck=second_bot.deck,
            seed=seed,
            timeout_seconds=timeout_seconds,
            format_name=format_name,
            game_number=game_number,
            goes_first=first_bot_id,
            game_log_path=game_log_path,
            game_meta={
                "year": year,
                "series_index": series_index,
                "game_number": game_number,
                "home_bot_id": home.bot_id,
                "home_name": home.name,
                "away_bot_id": away.bot_id,
                "away_name": away.name,
                "home_archetype": home.archetype,
                "away_archetype": away.archetype,
                "home_deck": home.deck.name,
                "away_deck": away.deck.name,
                "goes_first": first_bot_id,
                "duel_seed": list(seed),
            },
        )
        game_reports.append(report)
        wins = dict(report.get("wins_by_agent", {}))
        if int(wins.get(home.bot_id, 0)) > 0:
            home_wins += 1
            previous_game_loser = away.bot_id
        elif int(wins.get(away.bot_id, 0)) > 0:
            away_wins += 1
            previous_game_loser = home.bot_id
        else:
            previous_game_loser = None

    if home_wins > away_wins:
        series_winner = home.bot_id
    elif away_wins > home_wins:
        series_winner = away.bot_id
    else:
        series_winner = None

    return Bo3SeriesResult(
        home_bot=home.bot_id,
        away_bot=away.bot_id,
        series_winner=series_winner,
        home_series_wins=home_wins,
        away_series_wins=away_wins,
        games_played=games_per_matchup,
        game_reports=tuple(game_reports),
    )


def compute_standings(
    bots: Sequence[BotSeasonState],
    series_results: Sequence[Bo3SeriesResult],
) -> list[dict[str, Any]]:
    records: dict[str, dict[str, int]] = {
        bot.bot_id: {
            "bot_id": bot.bot_id,
            "name": bot.name,
            "series_wins": 0,
            "series_losses": 0,
            "series_ties": 0,
            "game_wins": 0,
            "game_losses": 0,
            "game_draws": 0,
        }
        for bot in bots
    }

    for series in series_results:
        for report in series.game_reports:
            wins = dict(report.get("wins_by_agent", {}))
            draws = int(report.get("draws", 0))
            for bot_id in (series.home_bot, series.away_bot):
                records[bot_id]["game_wins"] += int(wins.get(bot_id, 0))
                opponent = series.away_bot if bot_id == series.home_bot else series.home_bot
                records[bot_id]["game_losses"] += int(wins.get(opponent, 0))
                if draws:
                    records[bot_id]["game_draws"] += draws

        if series.series_winner is None:
            records[series.home_bot]["series_ties"] += 1
            records[series.away_bot]["series_ties"] += 1
        else:
            loser = series.away_bot if series.series_winner == series.home_bot else series.home_bot
            records[series.series_winner]["series_wins"] += 1
            records[loser]["series_losses"] += 1

    standings = list(records.values())
    for row in standings:
        played = row["series_wins"] + row["series_losses"] + row["series_ties"]
        row["series_win_rate"] = row["series_wins"] / played if played else 0.0
        decisive = row["game_wins"] + row["game_losses"]
        row["game_decisive_win_rate"] = row["game_wins"] / decisive if decisive else 0.0

    return sorted(
        standings,
        key=lambda row: (
            int(row["series_wins"]),
            float(row["series_win_rate"]),
            int(row["game_wins"]),
        ),
        reverse=True,
    )


def apply_post_season_learning(
    *,
    year: int,
    bots: Sequence[BotSeasonState],
    league_report: dict[str, Any],
    bot_reports: dict[str, dict[str, Any]],
    ethan_bot_id: str,
    learn_fn: LearnFn,
    combine_weights_fn: CombineWeightsFn,
    write_policy_fn: WritePolicyFn,
    load_policy_weights_fn: LoadPolicyWeightsFn,
    backup_policy_fn: Callable[[Path], Path] | None = None,
    accept_policy_update_fn: Callable[[dict[str, Any], dict[str, float], dict[str, float]], bool] | None = None,
    learn_league_fn: LearnFn | None = None,
) -> dict[str, Any]:
    learning_summary: dict[str, Any] = {"year": year, "bots": {}}
    other_policies = [bot.policy_path for bot in bots if bot.bot_id != ethan_bot_id]

    for bot in bots:
        report_path = bot.policy_path.parent / f"season-{year}-learning-report.json"
        report_path.write_text(json.dumps(bot_reports[bot.bot_id], indent=2, sort_keys=True) + "\n", encoding="utf-8")

        from ygotrainingbot.policy_runtime import raw_tag_weights, reset_cycle_observations, restore_policy

        backup_path = backup_policy_fn(bot.policy_path) if backup_policy_fn else None
        before_weights = raw_tag_weights(bot.policy_path)
        cycle_observations = reset_cycle_observations(bot.policy_path)

        bot_summary: dict[str, Any] = {
            "own_report": str(report_path),
            "backup_policy": str(backup_path) if backup_path else None,
            "cycle_observations_baseline": cycle_observations,
        }

        collective_baseline: dict[str, float] | None = None
        if bot.bot_id == ethan_bot_id:
            collective = combine_weights_fn(bot.policy_path, other_policies)
            collective_path = bot.policy_path.parent / f"season-{year}-collective-policy.json"
            write_policy_fn(collective_path, collective)
            write_policy_fn(bot.policy_path, collective)
            collective_baseline = raw_tag_weights(bot.policy_path)
            learn_fn(report_path, bot.policy_path)
            league_path = bot.policy_path.parent / f"season-{year}-league-wide-report.json"
            league_path.write_text(json.dumps(league_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            league_learn = learn_league_fn or learn_fn
            league_learn(league_path, bot.policy_path)
            bot_summary.update(
                {
                    "mode": "collective+own",
                    "collective_policy": str(collective_path),
                    "league_report": str(league_path),
                }
            )
        else:
            learn_fn(report_path, bot.policy_path)
            bot_summary["mode"] = "own-matches-only"

        after_weights = raw_tag_weights(bot.policy_path)
        promotion_report = dict(bot_reports[bot.bot_id])
        promotion_report.setdefault("bot_agent", bot.bot_id)
        gate_before = collective_baseline if collective_baseline is not None else before_weights
        accepted = True
        if accept_policy_update_fn is not None and backup_path is not None:
            accepted = accept_policy_update_fn(promotion_report, gate_before, after_weights)
            if not accepted:
                if collective_baseline is not None:
                    write_policy_fn(bot.policy_path, collective_baseline)
                    after_weights = collective_baseline
                    bot_summary["reverted_learn_only"] = True
                elif backup_path.is_file():
                    restore_policy(bot.policy_path, backup_path)
                    after_weights = before_weights
                    bot_summary["reverted_learn_only"] = False
        bot_summary["promoted"] = accepted
        bot_summary["reverted"] = not accepted
        bot_summary["weight_delta"] = _weight_delta_summary(before_weights, after_weights)
        if collective_baseline is not None:
            bot_summary["learn_delta"] = _weight_delta_summary(collective_baseline, after_weights)
        learning_summary["bots"][bot.bot_id] = bot_summary

    return learning_summary


def _weight_delta_summary(before: dict[str, float], after: dict[str, float]) -> dict[str, Any]:
    keys = sorted(set(before) | set(after))
    changed = {
        key: round(float(after.get(key, 0.0)) - float(before.get(key, 0.0)), 4)
        for key in keys
        if abs(float(after.get(key, 0.0)) - float(before.get(key, 0.0))) > 1e-6
    }
    return {
        "tags_changed": len(changed),
        "top_changes": sorted(changed.items(), key=lambda item: abs(item[1]), reverse=True)[:8],
    }


def run_season_year(
    *,
    year: int,
    bots: Sequence[BotSeasonState],
    series_per_opponent: int,
    play_duel: PlayDuelFn,
    build_gateway_command: Callable[[], str],
    timeout_seconds: float,
    progress_callback: Callable[[str], None] | None = None,
    rng: random.Random | None = None,
    games_dir: Path | None = None,
) -> dict[str, Any]:
    format_name = f"season-{year}"
    season_rng = rng or random.Random(year)
    resolved_games_dir = games_dir
    series_results: list[Bo3SeriesResult] = []
    all_game_reports: list[dict[str, Any]] = []
    bot_game_reports: dict[str, list[dict[str, Any]]] = {bot.bot_id: [] for bot in bots}
    matchup_rows: list[dict[str, Any]] = []

    gateway_command = build_gateway_command()
    total_series = len(bots) * (len(bots) - 1) // 2
    completed = 0

    for home_index, home in enumerate(bots):
        for away in bots[home_index + 1 :]:
            matchup_rng = random.Random(season_rng.randint(0, 2**63 - 1))
            matchup = play_fixed_games_matchup(
                play_duel=play_duel,
                gateway_command=gateway_command,
                home=home,
                away=away,
                timeout_seconds=timeout_seconds,
                format_name=format_name,
                rng=matchup_rng,
                games_per_matchup=series_per_opponent,
                games_dir=resolved_games_dir,
                year=year,
                series_index=0,
            )
            series_results.append(matchup)
            all_game_reports.extend(matchup.game_reports)
            bot_game_reports[home.bot_id].extend(matchup.game_reports)
            bot_game_reports[away.bot_id].extend(matchup.game_reports)

            ties = max(0, matchup.games_played - matchup.home_series_wins - matchup.away_series_wins)
            completed += 1
            if progress_callback:
                progress_callback(
                    f"[{year}] {completed}/{total_series} matchups "
                    f"({home.name} vs {away.name}: {matchup.home_series_wins}-{matchup.away_series_wins}-{ties})"
                )

            matchup_rows.append(
                {
                    "home_bot": home.bot_id,
                    "home_name": home.name,
                    "away_bot": away.bot_id,
                    "away_name": away.name,
                    "home_archetype": home.archetype,
                    "away_archetype": away.archetype,
                    "games_per_matchup": series_per_opponent,
                    "home_game_wins": matchup.home_series_wins,
                    "away_game_wins": matchup.away_series_wins,
                    "game_draws": ties,
                }
            )

    standings = compute_standings(bots, series_results)
    league_report = merge_training_reports(all_game_reports, format_name=format_name)
    bot_reports = {
        bot_id: merge_training_reports(
            [filter_report_for_bot(report, bot_id) for report in reports],
            format_name=f"{format_name}:{bot_id}",
            bot_agent=bot_id,
        )
        for bot_id, reports in bot_game_reports.items()
    }

    games_log_root = None
    if resolved_games_dir is not None:
        games_log_root = resolved_games_dir / str(year) / "games"

    return {
        "year": year,
        "format": format_name,
        "games_log_root": str(games_log_root) if games_log_root is not None else None,
        "series_per_opponent": series_per_opponent,
        "bots": [
            {
                "bot_id": bot.bot_id,
                "name": bot.name,
                "policy": bot.policy,
                "characteristics": bot.characteristics,
                "archetype": bot.archetype,
                "pack": str(bot.pack_path),
                "deck": bot.deck.name,
            }
            for bot in bots
        ],
        "matchups": matchup_rows,
        "standings": standings,
        "league_training_report": league_report,
        "bot_training_reports": bot_reports,
        "total_series": len(series_results),
        "total_games": sum(len(series.game_reports) for series in series_results),
    }


def run_yearly_bracket_tournament(
    *,
    profiles: Sequence[dict[str, object]],
    output_dir: Path,
    start_year: int,
    end_year: int,
    series_per_opponent: int,
    ethan_bot_id: str,
    play_duel: PlayDuelFn,
    build_gateway_command: Callable[[], str],
    materialize_pack: MaterializePackFn,
    learn_fn: LearnFn,
    combine_weights_fn: CombineWeightsFn,
    write_policy_fn: WritePolicyFn,
    load_policy_weights_fn: LoadPolicyWeightsFn,
    write_initial_policy_fn: Callable[[Path, dict[str, float]], None],
    timeout_seconds: float,
    progress_callback: Callable[[str], None] | None = None,
    master_seed: int | None = None,
    learn_league_fn: LearnFn | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    season_summaries: list[dict[str, Any]] = []

    for year in range(start_year, end_year + 1):
        year_dir = output_dir / str(year)
        bracket_path = year_dir / "bracket-results.json"
        if bracket_path.exists():
            season_summaries.append(json.loads(bracket_path.read_text(encoding="utf-8")))
            if progress_callback:
                progress_callback(f"[{year}] skipped (existing results)")
            continue

        bots: list[BotSeasonState] = []
        for profile in profiles:
            bot_id = str(profile["bot_id"])
            policy_path = output_dir / "bots" / bot_id / "policy.json"
            policy_path.parent.mkdir(parents=True, exist_ok=True)
            if not policy_path.exists():
                write_initial_policy_fn(policy_path, dict(profile.get("initial_weights", {})))

            pack_path, deck, archetype = resolve_year_deck(bot_id, year, dict(profile))
            materialize_pack(pack_path, deck, year_dir / "packs" / f"{bot_id}.json")

            bots.append(
                BotSeasonState(
                    bot_id=bot_id,
                    name=str(profile.get("name", bot_id)),
                    policy=str(profile["policy"]),
                    characteristics=str(profile.get("characteristics", "")),
                    policy_path=policy_path,
                    archetype=archetype,
                    pack_path=pack_path,
                    deck=deck,
                )
            )

        season = run_season_year(
            year=year,
            bots=bots,
            series_per_opponent=series_per_opponent,
            play_duel=play_duel,
            build_gateway_command=build_gateway_command,
            timeout_seconds=timeout_seconds,
            progress_callback=progress_callback,
            rng=random.Random((master_seed if master_seed is not None else 0) ^ year),
            games_dir=output_dir,
        )

        from ygotrainingbot.policy_runtime import backup_policy, should_accept_policy_update
        from ygotrainingbot.progress import record_protagonist_progress

        learning = apply_post_season_learning(
            year=year,
            bots=bots,
            league_report=season["league_training_report"],
            bot_reports=season["bot_training_reports"],
            ethan_bot_id=ethan_bot_id,
            learn_fn=learn_fn,
            learn_league_fn=learn_league_fn,
            combine_weights_fn=combine_weights_fn,
            write_policy_fn=write_policy_fn,
            load_policy_weights_fn=load_policy_weights_fn,
            backup_policy_fn=backup_policy,
            accept_policy_update_fn=should_accept_policy_update,
        )
        season["learning"] = learning
        protagonist_policy = output_dir / "bots" / ethan_bot_id / "policy.json"
        season["protagonist_progress"] = record_protagonist_progress(
            output_dir,
            year=year,
            ethan_bot_id=ethan_bot_id,
            season=season,
            policy_path=protagonist_policy,
        )
        season["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        year_dir.mkdir(parents=True, exist_ok=True)
        bracket_path.write_text(json.dumps(season, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _write_standings_markdown(year_dir / "standings.md", season)
        season_summaries.append(season)
        if progress_callback:
            progress_callback(f"[{year}] season complete — wrote {bracket_path}")

    tournament_report = {
        "start_year": start_year,
        "end_year": end_year,
        "series_per_opponent": series_per_opponent,
        "ethan_bot_id": ethan_bot_id,
        "seasons": season_summaries,
    }
    (output_dir / "tournament-report.json").write_text(
        json.dumps(tournament_report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return tournament_report


def _write_standings_markdown(path: Path, season: dict[str, Any]) -> None:
    lines = [
        f"# {season['year']} bracket standings",
        "",
        f"- Match format: {season['series_per_opponent']} games per unordered matchup",
        f"- Total matchups: {season['total_series']}",
        f"- Total games: {season['total_games']}",
        "",
        "| Rank | Bot | Series W | Series L | Series T | Series WR | Game W | Game L |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, row in enumerate(season["standings"], start=1):
        lines.append(
            f"| {index} | {row['name']} ({row['bot_id']}) | {row['series_wins']} | "
            f"{row['series_losses']} | {row['series_ties']} | {row['series_win_rate']:.3f} | "
            f"{row['game_wins']} | {row['game_losses']} |"
        )
    lines.append("")
    lines.append("## Decks")
    for bot in season["bots"]:
        lines.append(f"- **{bot['name']}** — {bot['archetype']} (`{bot['deck']}`)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
