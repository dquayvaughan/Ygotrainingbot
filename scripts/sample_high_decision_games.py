"""Replay 2010 bracket matchups and summarize the highest-decision games."""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ygotrainingbot.cli import (  # noqa: E402
    _gateway_command_base,
    _load_policy_weights,
    _play_single_duel_report,
)
from ygotrainingbot.format_training import FormatDeck  # noqa: E402
from ygotrainingbot.league_tournament import (  # noqa: E402
    BotSeasonState,
    pick_first_player_for_game,
    play_bo3_series,
    resolve_year_deck,
)

ROSTER = ROOT / "configs/league-rosters/progression-ycs-regionals.json"
EDOPRO_HOME = ROOT / ".ygotrain/edopro-home"
GATEWAY_SCRIPT = ROOT / "gateways/edopro-ocgcore/gateway.mjs"
YEAR = 2010
MAX_DECISIONS = 300
TIMEOUT = 45.0

# Directed pairs to sample (home_id, away_id) — mix of sweeps and close records from v3 log.
MATCHUPS = [
    ("bot-01", "bot-02"),  # Ethan vs Marcus
    ("bot-02", "bot-03"),  # Marcus vs Noah
    ("bot-04", "bot-06"),  # Tyler vs Brandon
    ("bot-07", "bot-08"),  # Derek vs Logan
    ("bot-01", "bot-04"),  # Ethan vs Tyler
]


def load_profiles() -> dict[str, dict]:
    payload = json.loads(ROSTER.read_text(encoding="utf-8"))
    return {str(bot["bot_id"]): dict(bot) for bot in payload["bots"]}


def bot_state(profile: dict, policy_path: Path) -> BotSeasonState:
    bot_id = str(profile["bot_id"])
    pack_path, deck, archetype = resolve_year_deck(bot_id, YEAR, profile)
    return BotSeasonState(
        bot_id=bot_id,
        name=str(profile["name"]),
        policy=str(profile["policy"]),
        characteristics=str(profile.get("characteristics", "")),
        policy_path=policy_path,
        archetype=archetype,
        pack_path=pack_path,
        deck=deck,
    )


def play_duel(gateway_command: str, **kwargs) -> dict:
    return _play_single_duel_report(
        gateway_command,
        first_agent=kwargs["first_agent"],
        second_agent=kwargs["second_agent"],
        agent_a_policy=kwargs["first_policy"],
        agent_b_policy=kwargs["second_policy"],
        agent_a_weights=kwargs["first_weights"],
        agent_b_weights=kwargs["second_weights"],
        deck_a=kwargs["first_deck"].main,
        deck_b=kwargs["second_deck"].main,
        seed=kwargs["seed"],
        timeout_seconds=kwargs["timeout_seconds"],
        format_name=kwargs["format_name"],
    )


def summarize_game(
    report: dict,
    *,
    home: BotSeasonState,
    away: BotSeasonState,
    series_index: int,
    game_meta: dict,
) -> dict:
    wins = dict(report.get("wins_by_agent", {}))
    winner = next(iter(wins), None) if len(wins) == 1 else None
    tags = Counter(dict(report.get("tags", {})))
    actions = Counter(dict(report.get("action_counts", {})))
    adjudication = [
        tag
        for tag in tags
        if "adjudication" in tag or tag in {"draw", "max-decisions", "retry-adjudication"}
    ]
    samples = list(report.get("decision_samples", []))
    key_choices = []
    for sample in samples[:12]:
        key_choices.append(
            {
                "turn": sample.get("turn"),
                "agent": sample.get("agent"),
                "action": sample.get("selected_label", sample.get("selected_action")),
                "tags": sample.get("selected_tags", [])[:6],
            }
        )
    return {
        "series_index": series_index,
        "home": f"{home.name} ({home.archetype} / {home.deck.name})",
        "away": f"{away.name} ({away.archetype} / {away.deck.name})",
        "game_number": game_meta.get("game_number"),
        "goes_first": game_meta.get("goes_first"),
        "duel_seed": report.get("duel_seed"),
        "traced_decisions": report.get("traced_decisions", 0),
        "winner": winner,
        "policies": f"{home.policy} vs {away.policy}",
        "adjudication_tags": adjudication,
        "top_actions": actions.most_common(8),
        "top_tags": tags.most_common(10),
        "key_choices": key_choices,
        "hit_max_decisions": report.get("traced_decisions", 0) >= MAX_DECISIONS,
    }


def collect_games() -> list[tuple[dict, dict, BotSeasonState, BotSeasonState, int]]:
    profiles = load_profiles()
    gateway = _gateway_command_base(
        GATEWAY_SCRIPT,
        edopro_home=EDOPRO_HOME,
        max_decisions=MAX_DECISIONS,
    )
    policy_root = ROOT / "data" / "game-samples" / "policies"
    collected: list[tuple[dict, dict, BotSeasonState, BotSeasonState, int]] = []

    for home_id, away_id in MATCHUPS:
        home = bot_state(profiles[home_id], policy_root / home_id / "policy.json")
        away = bot_state(profiles[away_id], policy_root / away_id / "policy.json")
        home.policy_path.parent.mkdir(parents=True, exist_ok=True)
        away.policy_path.parent.mkdir(parents=True, exist_ok=True)
        if not home.policy_path.exists():
            home.policy_path.write_text(
                json.dumps({"tag_weights": profiles[home_id].get("initial_weights", {}), "observations": 0})
                + "\n",
                encoding="utf-8",
            )
        if not away.policy_path.exists():
            away.policy_path.write_text(
                json.dumps({"tag_weights": profiles[away_id].get("initial_weights", {}), "observations": 0})
                + "\n",
                encoding="utf-8",
            )

        for series_index in range(3):
            series_rng = random.Random(series_index * 1000 + hash((home_id, away_id)))
            def play_duel_wrapper(cmd: str, **kw) -> dict:
                report = play_duel(
                    cmd,
                    first_agent=kw["first_agent"],
                    second_agent=kw["second_agent"],
                    first_policy=kw["first_policy"],
                    second_policy=kw["second_policy"],
                    first_weights=kw["first_weights"],
                    second_weights=kw["second_weights"],
                    first_deck=kw["first_deck"],
                    second_deck=kw["second_deck"],
                    seed=kw["seed"],
                    timeout_seconds=TIMEOUT,
                    format_name=f"sample-{YEAR}",
                )
                report["game_number"] = kw.get("game_number")
                report["goes_first"] = kw.get("goes_first")
                return report

            series = play_bo3_series(
                play_duel=play_duel_wrapper,
                gateway_command=gateway,
                home=home,
                away=away,
                timeout_seconds=TIMEOUT,
                format_name=f"sample-{YEAR}",
                rng=series_rng,
            )
            for game_number, report in enumerate(series.game_reports, start=1):
                meta = {
                    "game_number": report.get("game_number", game_number),
                    "goes_first": report.get("goes_first"),
                }
                collected.append((report, meta, home, away, series_index))

    return collected


def main() -> None:
    print("Sampling 2010 bracket matchups (3 Bo3 series per pair)...", flush=True)
    games = collect_games()
    ranked = sorted(games, key=lambda item: int(item[0].get("traced_decisions", 0)), reverse=True)
    top = ranked[:4]
    summaries = [
        summarize_game(report, home=home, away=away, series_index=idx, game_meta=meta)
        for report, meta, home, away, idx in top
    ]
    out_path = ROOT / "data" / "game-samples" / "top-decision-game-summaries.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
