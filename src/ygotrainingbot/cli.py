"""Command line tools for bootstrapping and running training passes."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Sequence

from ygotrainingbot.agents import create_agent
from ygotrainingbot.data import (
    build_card_sets,
    fetch_ygoprodeck_cards,
    load_card_database,
    save_card_database,
)
from ygotrainingbot.edopro import EdoproGatewayConfig, EdoproInstall, JsonLineEdoproSimulator
from ygotrainingbot.format_training import FormatDeck, load_format_pack, load_format_training_config
from ygotrainingbot.human_duels import (
    DEFAULT_CATALOG_DIR,
    build_learning_report,
    import_human_duels,
    write_learning_report,
)
from ygotrainingbot.learning import learn_from_report
from ygotrainingbot.script_health import count_script_runtime_errors, script_health_summary
from ygotrainingbot.static_training import StaticSetTrainer, StaticTrainingReport


class _LPPressureAgent:
    """Wrapper that biases legal choices toward causing battle damage."""

    def __init__(self, base_agent) -> None:
        self._base = base_agent

    @property
    def name(self) -> str:
        return str(getattr(self._base, "name", "lp-pressure"))

    def choose_action(self, state):
        actions = list(state.legal_actions)
        if not actions:
            raise ValueError(f"State {state.state_id!r} has no legal actions.")

        def pick(predicate):
            return next((action for action in actions if predicate(action)), None)

        preferred = (
            pick(lambda action: action.action_id.startswith("attack-"))
            or pick(lambda action: action.action_id == "to-battle-phase")
            or pick(lambda action: action.action_id.startswith("normal-summon-"))
            or pick(lambda action: action.action_id.startswith("special-summon-"))
            or pick(lambda action: action.action_id.startswith("activate-"))
            or pick(lambda action: action.action_id == "activate-effect")
        )
        if preferred is not None:
            return preferred

        chosen = self._base.choose_action(state)
        if chosen.action_id == "to-end-phase":
            proactive = pick(
                lambda action: action.action_id != "to-end-phase"
                and "decline" not in action.action_id
                and "phase" not in action.action_id
            )
            if proactive is not None:
                return proactive
        return chosen


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ygotrain")
    subcommands = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subcommands.add_parser(
        "fetch-cards",
        help="Fetch the current public Yu-Gi-Oh! card database into a local cache.",
    )
    fetch_parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/cards.json"),
        help="Where to write the card database cache.",
    )

    train_parser = subcommands.add_parser(
        "train-static",
        help="Run an immediate static training pass over cached card data.",
    )
    train_parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/cards.json"),
        help="Card database cache created by fetch-cards.",
    )
    train_parser.add_argument("--max-sets", type=int, default=None)
    train_parser.add_argument("--max-candidates-per-set", type=int, default=10)
    train_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a human summary.",
    )

    check_edopro_parser = subcommands.add_parser(
        "check-edopro",
        help="Validate paths for a local EDOPro install or data directory.",
    )
    check_edopro_parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="EDOPro root directory. Defaults to EDOPRO_HOME or the current directory.",
    )
    check_edopro_parser.add_argument(
        "--bin",
        type=Path,
        default=None,
        help="Optional EDOPro executable path. Defaults to EDOPRO_BIN when set.",
    )

    edopro_once_parser = subcommands.add_parser(
        "edopro-play-once",
        help="Run one duel through a JSON-lines EDOPro headless gateway.",
    )
    edopro_once_parser.add_argument(
        "--gateway-command",
        required=True,
        help="Command that starts the EDOPro-core-compatible JSON-lines gateway.",
    )
    edopro_once_parser.add_argument("--first-agent", default="bot-a")
    edopro_once_parser.add_argument("--second-agent", default="bot-b")
    edopro_once_parser.add_argument("--timeout-seconds", type=float, default=30.0)

    edopro_train_parser = subcommands.add_parser(
        "edopro-train",
        help="Run repeated duels through a JSON-lines EDOPro headless gateway.",
    )
    edopro_train_parser.add_argument(
        "--gateway-command",
        required=True,
        help="Command that starts the EDOPro-core-compatible JSON-lines gateway.",
    )
    edopro_train_parser.add_argument("--games", type=int, default=10)
    edopro_train_parser.add_argument("--first-agent", default="bot-a")
    edopro_train_parser.add_argument("--second-agent", default="bot-b")
    edopro_train_parser.add_argument("--agent-a-policy", default="first-legal")
    edopro_train_parser.add_argument("--agent-b-policy", default="first-legal")
    edopro_train_parser.add_argument("--agent-a-weights", type=Path, default=None)
    edopro_train_parser.add_argument("--agent-b-weights", type=Path, default=None)
    edopro_train_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    edopro_train_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON file for the gameplay training report.",
    )

    train_format_parser = subcommands.add_parser(
        "train-format",
        help="Train a named format from a JSON format config.",
    )
    train_format_parser.add_argument("--config", type=Path, required=True)
    train_format_parser.add_argument(
        "--edopro-home",
        type=Path,
        required=True,
        help="EDOPro-compatible data home created by scripts/bootstrap_edopro_home.sh.",
    )
    train_format_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    train_format_parser.add_argument("--games", type=int, default=None)
    train_format_parser.add_argument("--max-decisions", type=int, default=None)
    train_format_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    train_format_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/format-training-report.json"),
    )

    train_pack_parser = subcommands.add_parser(
        "train-format-pack",
        help="Train every matchup in a format pack with banlist metadata and multiple decks.",
    )
    train_pack_parser.add_argument("--pack", type=Path, required=True)
    train_pack_parser.add_argument("--edopro-home", type=Path, required=True)
    train_pack_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    train_pack_parser.add_argument("--games-per-matchup", type=int, default=None)
    train_pack_parser.add_argument("--max-decisions", type=int, default=None)
    train_pack_parser.add_argument("--timeout-seconds", type=float, default=300.0)
    train_pack_parser.add_argument("--agent-a-policy", default="heuristic")
    train_pack_parser.add_argument("--agent-b-policy", default="heuristic")
    train_pack_parser.add_argument("--agent-a-weights", type=Path, default=None)
    train_pack_parser.add_argument("--agent-b-weights", type=Path, default=None)
    train_pack_parser.add_argument(
        "--deck-a-name",
        default=None,
        help="Only run matchups that include this deck as deck A.",
    )
    train_pack_parser.add_argument(
        "--custom-deck-a-file",
        type=Path,
        default=None,
        help="JSON deck list (imported .ydk) to use as deck A.",
    )
    train_pack_parser.add_argument(
        "--deck-b-name",
        default=None,
        help="Only run matchups that include this deck as deck B.",
    )
    train_pack_parser.add_argument(
        "--custom-deck-b-file",
        type=Path,
        default=None,
        help="JSON deck list (imported .ydk) to use as deck B.",
    )
    train_pack_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/format-pack-training-report.json"),
    )

    compare_parser = subcommands.add_parser(
        "compare-agents",
        help="Compare two agent policies on a format pack and report win rates.",
    )
    compare_parser.add_argument("--pack", type=Path, required=True)
    compare_parser.add_argument("--edopro-home", type=Path, required=True)
    compare_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    compare_parser.add_argument("--candidate-policy", default="heuristic")
    compare_parser.add_argument("--baseline-policy", default="first-legal")
    compare_parser.add_argument("--candidate-weights", type=Path, default=None)
    compare_parser.add_argument("--baseline-weights", type=Path, default=None)
    compare_parser.add_argument("--games-per-matchup", type=int, default=5)
    compare_parser.add_argument("--max-decisions", type=int, default=None)
    compare_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    compare_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/agent-comparison-report.json"),
    )

    benchmark_parser = subcommands.add_parser(
        "benchmark-agents",
        help="Benchmark several agent policies against a baseline on a format pack.",
    )
    benchmark_parser.add_argument("--pack", type=Path, required=True)
    benchmark_parser.add_argument("--edopro-home", type=Path, required=True)
    benchmark_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    benchmark_parser.add_argument(
        "--policies",
        default="random,heuristic,aggressive,tempo,control",
        help="Comma-separated candidate policies to compare.",
    )
    benchmark_parser.add_argument("--baseline-policy", default="first-legal")
    benchmark_parser.add_argument("--games-per-matchup", type=int, default=5)
    benchmark_parser.add_argument("--max-decisions", type=int, default=None)
    benchmark_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    benchmark_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/agent-benchmark-report.json"),
    )

    learn_parser = subcommands.add_parser(
        "learn-from-report",
        help="Update simple policy weights and write a plain-English learning report.",
    )
    learn_parser.add_argument("--report", type=Path, required=True)
    learn_parser.add_argument(
        "--policy",
        type=Path,
        default=Path("data/learned-policy.json"),
        help="JSON policy-weight file to update.",
    )
    learn_parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/learning-summary.txt"),
        help="Plain-English summary output path.",
    )

    import_human_parser = subcommands.add_parser(
        "import-human-duels",
        help="Import JSON duel logs from real players into data/human-duels/.",
    )
    import_human_parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Directory containing .json duel logs (full game logs or decisions-only).",
    )
    import_human_parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=DEFAULT_CATALOG_DIR,
        help="Catalog root (manifest + duels/ copies).",
    )
    import_human_parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Register paths in manifest without copying files into the catalog.",
    )

    learn_human_parser = subcommands.add_parser(
        "learn-from-human-duels",
        help="Build a training report from imported human duels and update policy weights.",
    )
    learn_human_parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=DEFAULT_CATALOG_DIR,
    )
    learn_human_parser.add_argument(
        "--study-agent",
        default=None,
        help="Only learn from this player id (must match decision 'agent' fields).",
    )
    learn_human_parser.add_argument(
        "--format",
        dest="format_filter",
        default=None,
        help="Only include duels tagged with this format in meta.format.",
    )
    learn_human_parser.add_argument(
        "--bot-agent",
        default=None,
        help="Alias stored on the report as bot_agent (defaults to --study-agent).",
    )
    learn_human_parser.add_argument(
        "--policy",
        type=Path,
        default=Path("data/learned-policy.json"),
    )
    learn_human_parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/human-duels/learning-summary.txt"),
    )
    learn_human_parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path for the generated training report JSON.",
    )

    promote_parser = subcommands.add_parser(
        "promote-learned-policy",
        help="Compare an unweighted policy against learned weights and mark promotion status.",
    )
    promote_parser.add_argument("--pack", type=Path, required=True)
    promote_parser.add_argument("--edopro-home", type=Path, required=True)
    promote_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    promote_parser.add_argument("--policy", default="heuristic")
    promote_parser.add_argument("--learned-policy", type=Path, required=True)
    promote_parser.add_argument("--games-per-matchup", type=int, default=5)
    promote_parser.add_argument("--max-decisions", type=int, default=None)
    promote_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    promote_parser.add_argument("--promote-to", type=Path, default=None)
    promote_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/promotion-report.json"),
    )

    loop_parser = subcommands.add_parser(
        "train-learn-promote",
        help="Run training, learn from the report, then benchmark promotion in one loop.",
    )
    loop_parser.add_argument("--pack", type=Path, required=True)
    loop_parser.add_argument("--edopro-home", type=Path, required=True)
    loop_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    loop_parser.add_argument("--policy", default="heuristic")
    loop_parser.add_argument("--current-policy", type=Path, default=Path("data/promoted-policy.json"))
    loop_parser.add_argument("--candidate-policy", type=Path, default=None)
    loop_parser.add_argument("--promote-to", type=Path, default=None)
    loop_parser.add_argument("--games-per-matchup", type=int, default=5)
    loop_parser.add_argument("--promotion-games-per-matchup", type=int, default=None)
    loop_parser.add_argument("--max-decisions", type=int, default=None)
    loop_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    loop_parser.add_argument("--output-dir", type=Path, default=Path("data/learning-loop"))

    curriculum_parser = subcommands.add_parser(
        "train-format-curriculum",
        help="Train across multiple formats in sequence, carrying forward learned policy.",
    )
    curriculum_parser.add_argument("--packs", type=Path, nargs="+", required=True)
    curriculum_parser.add_argument("--edopro-home", type=Path, required=True)
    curriculum_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    curriculum_parser.add_argument("--policy", default="heuristic")
    curriculum_parser.add_argument("--current-policy", type=Path, default=Path("data/promoted-policy.json"))
    curriculum_parser.add_argument("--games-per-matchup", type=int, default=5)
    curriculum_parser.add_argument("--promotion-games-per-matchup", type=int, default=None)
    curriculum_parser.add_argument("--max-decisions", type=int, default=None)
    curriculum_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    curriculum_parser.add_argument("--promote-to", type=Path, default=None)
    curriculum_parser.add_argument("--output-dir", type=Path, default=Path("data/format-curriculum"))

    league_parser = subcommands.add_parser(
        "train-bot-league",
        help="Train multiple independent opponent bots and evaluate the main bot against them.",
    )
    league_parser.add_argument("--packs", type=Path, nargs="+", required=True)
    league_parser.add_argument("--edopro-home", type=Path, required=True)
    league_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    league_parser.add_argument("--main-policy", default="aggressive")
    league_parser.add_argument("--main-weights", type=Path, default=Path("data/promoted-policy-v3.json"))
    league_parser.add_argument("--opponent-policy", default="aggressive")
    league_parser.add_argument("--opponents", type=int, default=9)
    league_parser.add_argument("--games-per-matchup", type=int, default=8)
    league_parser.add_argument("--promotion-games-per-matchup", type=int, default=None)
    league_parser.add_argument("--evaluation-games-per-matchup", type=int, default=8)
    league_parser.add_argument("--max-decisions", type=int, default=600)
    league_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    league_parser.add_argument("--output-dir", type=Path, default=Path("data/bot-league"))
    league_parser.add_argument("--roster-path", type=Path, default=None)

    bracket_parser = subcommands.add_parser(
        "run-yearly-bracket",
        help="Run year-by-year bracket seasons with per-bot learning (Yugi/bot-01 learns from all).",
    )
    bracket_parser.add_argument("--roster-path", type=Path, required=True)
    bracket_parser.add_argument("--edopro-home", type=Path, required=True)
    bracket_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    bracket_parser.add_argument("--start-year", type=int, default=2010)
    bracket_parser.add_argument("--end-year", type=int, default=2025)
    bracket_parser.add_argument("--series-per-opponent", type=int, default=10)
    bracket_parser.add_argument("--max-decisions", type=int, default=600)
    bracket_parser.add_argument("--timeout-seconds", type=float, default=300.0)
    bracket_parser.add_argument("--ethan-bot-id", default="bot-01")
    bracket_parser.add_argument("--default-runtime-profile", default="balanced")
    bracket_parser.add_argument("--ethan-runtime-profile", default="balanced")
    bracket_parser.add_argument("--master-seed", type=int, default=None)
    bracket_parser.add_argument("--output-dir", type=Path, default=Path("data/yearly-bracket"))
    bracket_parser.add_argument(
        "--allow-script-errors",
        action="store_true",
        help="Allow duels with Lua/script runtime errors (default: reject and retry).",
    )

    loop_parser = subcommands.add_parser(
        "run-yearly-bracket-loop",
        help="Run repeated bracket seasons (keeps learned policies, clears per-cycle game logs).",
    )
    loop_parser.add_argument("--roster-path", type=Path, required=True)
    loop_parser.add_argument("--edopro-home", type=Path, required=True)
    loop_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    loop_parser.add_argument("--year", type=int, default=2010, help="Format year to replay each cycle.")
    loop_parser.add_argument("--cycles", type=int, default=50)
    loop_parser.add_argument("--series-per-opponent", type=int, default=10)
    loop_parser.add_argument("--max-decisions", type=int, default=600)
    loop_parser.add_argument("--timeout-seconds", type=float, default=120.0)
    loop_parser.add_argument("--ethan-bot-id", default="bot-01")
    loop_parser.add_argument("--master-seed", type=int, default=2010)
    loop_parser.add_argument("--output-dir", type=Path, default=Path("data/yearly-bracket-2010-clean"))
    loop_parser.add_argument("--pause-seconds", type=float, default=5.0)
    loop_parser.add_argument("--max-retries-per-cycle", type=int, default=2)
    loop_parser.add_argument("--allow-script-errors", action="store_true")
    loop_parser.add_argument(
        "--min-free-gb",
        type=float,
        default=1.0,
        help="Abort a cycle if free disk space drops below this threshold.",
    )
    loop_parser.add_argument(
        "--sim-validate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="After learning, run paired sim validation and revert policy if candidate loses.",
    )
    loop_parser.add_argument(
        "--validation-games-per-matchup",
        type=int,
        default=2,
        help="Games per opponent when sim-validating protagonist policy updates.",
    )
    loop_parser.add_argument(
        "--validation-opponent-count",
        type=int,
        default=3,
        help="Number of league opponents to use for sim validation.",
    )
    loop_parser.add_argument(
        "--stop-on-regression",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stop the loop early if protagonist series win rate drops beyond tolerance.",
    )
    loop_parser.add_argument(
        "--regression-tolerance",
        type=float,
        default=0.05,
        help="Maximum allowed series win-rate drop between consecutive ok cycles.",
    )
    loop_parser.add_argument(
        "--index-after-cycle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Index cycle game logs into output-dir/training.db after each ok cycle.",
    )

    index_db_parser = subcommands.add_parser(
        "index-training-db",
        help="Index game logs into a SQLite training database (Phase 4).",
    )
    index_db_parser.add_argument("--roots", type=Path, nargs="+", required=True)
    index_db_parser.add_argument("--db", type=Path, default=Path("data/training.db"))

    query_db_parser = subcommands.add_parser(
        "query-training-db",
        help="Query indexed duel records.",
    )
    query_db_parser.add_argument("--db", type=Path, default=Path("data/training.db"))
    query_db_parser.add_argument("--bot", default="bot-01")
    query_db_parser.add_argument("--opponent", default=None)
    query_db_parser.add_argument("--going-first", action="store_true")
    query_db_parser.add_argument("--going-second", action="store_true")
    query_db_parser.add_argument("--limit", type=int, default=10)

    analytics_parser = subcommands.add_parser(
        "deck-analytics",
        help="Summarize deck/decision analytics for a bot (Phase 5).",
    )
    analytics_parser.add_argument("--db", type=Path, default=Path("data/training.db"))
    analytics_parser.add_argument("--bot", default="bot-01")
    analytics_parser.add_argument("--apply-to-policy", type=Path, default=None)

    experiment_parser = subcommands.add_parser(
        "run-experiment",
        help="Run a head-to-head deck experiment with confidence intervals (Phase 6).",
    )
    experiment_parser.add_argument("--pack", type=Path, required=True)
    experiment_parser.add_argument("--deck-a", type=int, default=0, help="Deck index in the format pack.")
    experiment_parser.add_argument("--deck-b", type=int, default=1, help="Deck index in the format pack.")
    experiment_parser.add_argument("--games", type=int, default=20)
    experiment_parser.add_argument("--policy-a", default="search-control")
    experiment_parser.add_argument("--policy-b", default="control")
    experiment_parser.add_argument("--weights-a", type=Path, default=None)
    experiment_parser.add_argument("--weights-b", type=Path, default=None)
    experiment_parser.add_argument("--edopro-home", type=Path, required=True)
    experiment_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    experiment_parser.add_argument("--max-decisions", type=int, default=600)
    experiment_parser.add_argument("--timeout-seconds", type=float, default=120.0)
    experiment_parser.add_argument("--output", type=Path, default=Path("data/experiments/latest.json"))

    chrono_parser = subcommands.add_parser(
        "run-chronological-curriculum",
        help="Train Goat -> Edison chronologically, optionally run bracket years (Phase 7).",
    )
    chrono_parser.add_argument(
        "--packs",
        type=Path,
        nargs="*",
        default=None,
        help="Defaults to goat-2005 then edison-2010.",
    )
    chrono_parser.add_argument("--edopro-home", type=Path, required=True)
    chrono_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    chrono_parser.add_argument("--policy", default="search-control")
    chrono_parser.add_argument("--current-policy", type=Path, default=Path("data/promoted-policy.json"))
    chrono_parser.add_argument("--games-per-matchup", type=int, default=5)
    chrono_parser.add_argument("--promotion-games-per-matchup", type=int, default=None)
    chrono_parser.add_argument("--max-decisions", type=int, default=600)
    chrono_parser.add_argument("--timeout-seconds", type=float, default=120.0)
    chrono_parser.add_argument("--promote-to", type=Path, default=Path("data/chronological-policy.json"))
    chrono_parser.add_argument("--output-dir", type=Path, default=Path("data/chronological-curriculum"))
    chrono_parser.add_argument("--roster-path", type=Path, default=Path("configs/league-rosters/progression-ycs-regionals.json"))
    chrono_parser.add_argument("--bracket-year", type=int, action="append", default=None)
    chrono_parser.add_argument("--series-per-opponent", type=int, default=6)
    chrono_parser.add_argument("--ethan-bot-id", default="bot-01")

    ask_parser = subcommands.add_parser(
        "ask-training",
        help="Ask plain-English questions over indexed games and progress (Phase 8).",
    )
    ask_parser.add_argument("--db", type=Path, default=Path("data/training.db"))
    ask_parser.add_argument("--question", required=True)
    ask_parser.add_argument("--bot", default="bot-01")
    ask_parser.add_argument("--progress-dir", type=Path, default=None)

    matrix_parser = subcommands.add_parser(
        "test-format-matrix",
        help="Smoke-test every deck matchup across format packs (Goat, Edison, etc.).",
    )
    matrix_parser.add_argument(
        "--packs",
        type=Path,
        nargs="+",
        default=[
            Path("configs/format-packs/goat-2005.json"),
            Path("configs/format-packs/edison-2010.json"),
        ],
    )
    matrix_parser.add_argument("--edopro-home", type=Path, required=True)
    matrix_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    matrix_parser.add_argument("--games-per-matchup", type=int, default=1)
    matrix_parser.add_argument("--max-decisions", type=int, default=600)
    matrix_parser.add_argument("--timeout-seconds", type=float, default=300.0)
    matrix_parser.add_argument("--policy", default="search-control")
    matrix_parser.add_argument("--output", type=Path, default=Path("data/format-matrix-report.json"))

    watch_parser = subcommands.add_parser(
        "export-edopro-watch",
        help="Export random logged games as EDOPro watch bundles.",
    )
    watch_parser.add_argument("--bracket-dir", type=Path, required=True)
    watch_parser.add_argument("--output-dir", type=Path, default=None)
    watch_parser.add_argument("--year", type=int, default=2010)
    watch_parser.add_argument("--count", type=int, default=2)
    watch_parser.add_argument("--seed", type=int, default=None)
    watch_parser.add_argument("--edopro-deck-dir", type=Path, default=None)

    bracket_ab_parser = subcommands.add_parser(
        "run-yearly-bracket-ethan-ab",
        help="Run 2010 baseline vs Ethan-boosted brackets and write a comparison report.",
    )
    bracket_ab_parser.add_argument("--roster-path", type=Path, required=True)
    bracket_ab_parser.add_argument("--edopro-home", type=Path, required=True)
    bracket_ab_parser.add_argument(
        "--gateway-script",
        type=Path,
        default=Path("gateways/edopro-ocgcore/gateway.mjs"),
    )
    bracket_ab_parser.add_argument("--start-year", type=int, default=2010)
    bracket_ab_parser.add_argument("--end-year", type=int, default=2010)
    bracket_ab_parser.add_argument("--series-per-opponent", type=int, default=10)
    bracket_ab_parser.add_argument("--max-decisions", type=int, default=600)
    bracket_ab_parser.add_argument("--timeout-seconds", type=float, default=120.0)
    bracket_ab_parser.add_argument("--ethan-bot-id", default="bot-01")
    bracket_ab_parser.add_argument("--master-seed", type=int, default=None)
    bracket_ab_parser.add_argument("--baseline-output-dir", type=Path, default=Path("data/yearly-bracket-2010-v2"))
    bracket_ab_parser.add_argument("--boosted-output-dir", type=Path, default=Path("data/yearly-bracket-2010-ethan-elite"))
    bracket_ab_parser.add_argument("--comparison-output", type=Path, default=Path("data/yearly-bracket-2010-ab/ethan-deck-ab-report.json"))
    bracket_ab_parser.add_argument("--baseline-runtime-profile", default="balanced")
    bracket_ab_parser.add_argument("--ethan-boosted-runtime-profile", default="elite")
    bracket_ab_parser.add_argument("--wait-for-baseline-seconds", type=int, default=0)

    args = parser.parse_args(argv)
    if args.command == "fetch-cards":
        return _fetch_cards(args.cache)
    if args.command == "train-static":
        return _train_static(
            args.cache,
            max_sets=args.max_sets,
            max_candidates_per_set=args.max_candidates_per_set,
            as_json=args.json,
        )
    if args.command == "check-edopro":
        return _check_edopro(args.root, args.bin)
    if args.command == "edopro-play-once":
        return _edopro_play_once(
            args.gateway_command,
            first_agent=args.first_agent,
            second_agent=args.second_agent,
            timeout_seconds=args.timeout_seconds,
        )
    if args.command == "edopro-train":
        return _edopro_train(
            args.gateway_command,
            games=args.games,
            first_agent=args.first_agent,
            second_agent=args.second_agent,
            timeout_seconds=args.timeout_seconds,
            output=args.output,
            agent_a_policy=args.agent_a_policy,
            agent_b_policy=args.agent_b_policy,
            agent_a_weights=args.agent_a_weights,
            agent_b_weights=args.agent_b_weights,
        )
    if args.command == "train-format":
        return _train_format(
            args.config,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            games=args.games,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            output=args.output,
        )
    if args.command == "train-format-pack":
        return _train_format_pack(
            args.pack,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            games_per_matchup=args.games_per_matchup,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            output=args.output,
            agent_a_policy=args.agent_a_policy,
            agent_b_policy=args.agent_b_policy,
            agent_a_weights=args.agent_a_weights,
            agent_b_weights=args.agent_b_weights,
            deck_a_name=args.deck_a_name,
            deck_b_name=args.deck_b_name,
            custom_deck_a_file=args.custom_deck_a_file,
            custom_deck_b_file=args.custom_deck_b_file,
        )
    if args.command == "compare-agents":
        return _compare_agents(
            args.pack,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            candidate_policy=args.candidate_policy,
            baseline_policy=args.baseline_policy,
            candidate_weights=args.candidate_weights,
            baseline_weights=args.baseline_weights,
            games_per_matchup=args.games_per_matchup,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            output=args.output,
        )
    if args.command == "benchmark-agents":
        return _benchmark_agents(
            args.pack,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            policies=tuple(policy.strip() for policy in args.policies.split(",") if policy.strip()),
            baseline_policy=args.baseline_policy,
            games_per_matchup=args.games_per_matchup,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            output=args.output,
        )
    if args.command == "learn-from-report":
        return _learn_from_report(args.report, policy=args.policy, summary=args.summary)
    if args.command == "import-human-duels":
        return _import_human_duels(
            args.input_dir,
            catalog_dir=args.catalog_dir,
            copy_files=not args.no_copy,
        )
    if args.command == "learn-from-human-duels":
        return _learn_from_human_duels(
            catalog_dir=args.catalog_dir,
            study_agent=args.study_agent,
            format_filter=args.format_filter,
            bot_agent=args.bot_agent,
            policy=args.policy,
            summary=args.summary,
            report=args.report,
        )
    if args.command == "promote-learned-policy":
        return _promote_learned_policy(
            args.pack,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            policy=args.policy,
            learned_policy=args.learned_policy,
            games_per_matchup=args.games_per_matchup,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            promote_to=args.promote_to,
            output=args.output,
        )
    if args.command == "train-learn-promote":
        return _train_learn_promote(
            args.pack,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            policy=args.policy,
            current_policy=args.current_policy,
            candidate_policy=args.candidate_policy,
            promote_to=args.promote_to,
            games_per_matchup=args.games_per_matchup,
            promotion_games_per_matchup=args.promotion_games_per_matchup,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            output_dir=args.output_dir,
        )
    if args.command == "train-format-curriculum":
        return _train_format_curriculum(
            args.packs,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            policy=args.policy,
            current_policy=args.current_policy,
            games_per_matchup=args.games_per_matchup,
            promotion_games_per_matchup=args.promotion_games_per_matchup,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            promote_to=args.promote_to,
            output_dir=args.output_dir,
        )
    if args.command == "train-bot-league":
        return _train_bot_league(
            args.packs,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            main_policy=args.main_policy,
            main_weights=args.main_weights,
            opponent_policy=args.opponent_policy,
            opponents=args.opponents,
            games_per_matchup=args.games_per_matchup,
            promotion_games_per_matchup=args.promotion_games_per_matchup,
            evaluation_games_per_matchup=args.evaluation_games_per_matchup,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            output_dir=args.output_dir,
            roster_path=args.roster_path,
        )
    if args.command == "run-yearly-bracket-loop":
        return _run_yearly_bracket_loop(
            roster_path=args.roster_path,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            year=args.year,
            cycles=args.cycles,
            series_per_opponent=args.series_per_opponent,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            ethan_bot_id=args.ethan_bot_id,
            master_seed=args.master_seed,
            output_dir=args.output_dir,
            pause_seconds=args.pause_seconds,
            max_retries_per_cycle=args.max_retries_per_cycle,
            allow_script_errors=args.allow_script_errors,
            min_free_gb=args.min_free_gb,
            sim_validate=args.sim_validate,
            validation_games_per_matchup=args.validation_games_per_matchup,
            validation_opponent_count=args.validation_opponent_count,
            stop_on_regression=args.stop_on_regression,
            regression_tolerance=args.regression_tolerance,
            index_after_cycle=args.index_after_cycle,
        )
    if args.command == "run-yearly-bracket":
        return _run_yearly_bracket(
            roster_path=args.roster_path,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            start_year=args.start_year,
            end_year=args.end_year,
            series_per_opponent=args.series_per_opponent,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            ethan_bot_id=args.ethan_bot_id,
            default_runtime_profile=args.default_runtime_profile,
            ethan_runtime_profile=args.ethan_runtime_profile,
            master_seed=args.master_seed,
            output_dir=args.output_dir,
            allow_script_errors=args.allow_script_errors,
        )
    if args.command == "run-yearly-bracket-ethan-ab":
        return _run_yearly_bracket_ethan_ab(
            roster_path=args.roster_path,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            start_year=args.start_year,
            end_year=args.end_year,
            series_per_opponent=args.series_per_opponent,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            ethan_bot_id=args.ethan_bot_id,
            master_seed=args.master_seed,
            baseline_output_dir=args.baseline_output_dir,
            boosted_output_dir=args.boosted_output_dir,
            comparison_output=args.comparison_output,
            baseline_runtime_profile=args.baseline_runtime_profile,
            ethan_boosted_runtime_profile=args.ethan_boosted_runtime_profile,
            wait_for_baseline_seconds=args.wait_for_baseline_seconds,
        )
    if args.command == "index-training-db":
        return _index_training_db(args.roots, db_path=args.db)
    if args.command == "query-training-db":
        return _query_training_db(
            db_path=args.db,
            bot_id=args.bot,
            opponent=args.opponent,
            going_first=args.going_first,
            going_second=args.going_second,
            limit=args.limit,
        )
    if args.command == "deck-analytics":
        return _deck_analytics(
            db_path=args.db,
            bot_id=args.bot,
            apply_to_policy=args.apply_to_policy,
        )
    if args.command == "run-experiment":
        return _run_experiment(
            pack=args.pack,
            deck_a_index=args.deck_a,
            deck_b_index=args.deck_b,
            games=args.games,
            policy_a=args.policy_a,
            policy_b=args.policy_b,
            weights_a=args.weights_a,
            weights_b=args.weights_b,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            output=args.output,
        )
    if args.command == "run-chronological-curriculum":
        return _run_chronological_curriculum(
            packs=args.packs,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            policy=args.policy,
            current_policy=args.current_policy,
            games_per_matchup=args.games_per_matchup,
            promotion_games_per_matchup=args.promotion_games_per_matchup,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            promote_to=args.promote_to,
            output_dir=args.output_dir,
            roster_path=args.roster_path,
            bracket_years=args.bracket_year,
            series_per_opponent=args.series_per_opponent,
            ethan_bot_id=args.ethan_bot_id,
        )
    if args.command == "ask-training":
        return _ask_training(
            db_path=args.db,
            question=args.question,
            bot_id=args.bot,
            progress_dir=args.progress_dir,
        )
    if args.command == "test-format-matrix":
        return _test_format_matrix(
            packs=args.packs,
            edopro_home=args.edopro_home,
            gateway_script=args.gateway_script,
            games_per_matchup=args.games_per_matchup,
            max_decisions=args.max_decisions,
            timeout_seconds=args.timeout_seconds,
            policy=args.policy,
            output=args.output,
        )
    if args.command == "export-edopro-watch":
        return _export_edopro_watch(
            bracket_dir=args.bracket_dir,
            output_dir=args.output_dir,
            year=args.year,
            count=args.count,
            seed=args.seed,
            edopro_deck_dir=args.edopro_deck_dir,
        )
    raise ValueError(f"Unknown command {args.command!r}.")


def _learn_from_report(report: Path, *, policy: Path, summary: Path) -> int:
    _analysis, english = learn_from_report(report, policy)
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(english, encoding="utf-8")
    print(english)
    return 0


def _import_human_duels(input_dir: Path, *, catalog_dir: Path, copy_files: bool) -> int:
    result = import_human_duels(input_dir, catalog_dir=catalog_dir, copy_files=copy_files)
    payload = {
        "catalog_dir": str(result.catalog_dir),
        "imported": len(result.imported),
        "skipped": result.skipped,
        "errors": result.errors,
        "duels": [
            {
                "duel_id": entry.duel_id,
                "path": entry.path,
                "format": entry.format,
                "study_agent": entry.study_agent,
                "decision_count": entry.decision_count,
            }
            for entry in result.imported
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if result.errors else 0


def _learn_from_human_duels(
    *,
    catalog_dir: Path,
    study_agent: str | None,
    format_filter: str | None,
    bot_agent: str | None,
    policy: Path,
    summary: Path,
    report: Path | None,
) -> int:
    training_report = build_learning_report(
        catalog_dir,
        study_agent=study_agent,
        format_filter=format_filter,
        bot_agent=bot_agent,
    )
    report_path = report or write_learning_report(catalog_dir, training_report)
    if report is not None:
        report_path = report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(training_report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return _learn_from_report(report_path, policy=policy, summary=summary)


def _promote_learned_policy(
    pack: Path,
    *,
    edopro_home: Path,
    gateway_script: Path,
    policy: str,
    learned_policy: Path,
    games_per_matchup: int,
    max_decisions: int | None,
    timeout_seconds: float,
    promote_to: Path | None,
    output: Path,
) -> int:
    comparison = _compare_agents_report(
        pack,
        edopro_home=edopro_home,
        gateway_script=gateway_script,
        candidate_policy=policy,
        baseline_policy=policy,
        candidate_weights=learned_policy,
        baseline_weights=None,
        games_per_matchup=games_per_matchup,
        max_decisions=max_decisions,
        timeout_seconds=timeout_seconds,
    )
    candidate_wins = int(comparison["candidate_wins"])
    baseline_wins = int(comparison["baseline_wins"])
    candidate_rate = float(comparison["candidate_win_rate"])
    baseline_rate = float(comparison["baseline_win_rate"])
    promotable = candidate_wins > baseline_wins and candidate_rate >= baseline_rate
    promoted_to: str | None = None
    if promotable and promote_to is not None:
        promote_to.parent.mkdir(parents=True, exist_ok=True)
        promote_to.write_text(learned_policy.read_text(encoding="utf-8"), encoding="utf-8")
        promoted_to = str(promote_to)

    report = {
        "policy": policy,
        "learned_policy": str(learned_policy),
        "promotable": promotable,
        "promoted_to": promoted_to,
        "reason": (
            "Learned weights beat the unweighted policy."
            if promotable
            else "Learned weights did not beat the unweighted policy; do not promote yet."
        ),
        "comparison": comparison,
    }
    _write_report(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _train_learn_promote(
    pack: Path,
    *,
    edopro_home: Path,
    gateway_script: Path,
    policy: str,
    current_policy: Path,
    candidate_policy: Path | None,
    promote_to: Path | None,
    games_per_matchup: int,
    promotion_games_per_matchup: int | None,
    max_decisions: int | None,
    timeout_seconds: float,
    output_dir: Path,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    training_report = output_dir / "format-training-report.json"
    learning_summary = output_dir / "learning-summary.txt"
    promotion_report = output_dir / "promotion-report.json"
    candidate_policy = candidate_policy or output_dir / "learned-policy.json"
    promote_to = promote_to or output_dir / "promoted-policy.json"
    current_weights = current_policy if current_policy.exists() else None

    _train_format_pack(
        pack,
        edopro_home=edopro_home,
        gateway_script=gateway_script,
        games_per_matchup=games_per_matchup,
        max_decisions=max_decisions,
        timeout_seconds=timeout_seconds,
        output=training_report,
        agent_a_policy=policy,
        agent_b_policy=policy,
        agent_a_weights=current_weights,
        agent_b_weights=current_weights,
    )
    _learn_from_report(training_report, policy=candidate_policy, summary=learning_summary)
    _promote_learned_policy(
        pack,
        edopro_home=edopro_home,
        gateway_script=gateway_script,
        policy=policy,
        learned_policy=candidate_policy,
        games_per_matchup=promotion_games_per_matchup or games_per_matchup,
        max_decisions=max_decisions,
        timeout_seconds=timeout_seconds,
        promote_to=promote_to,
        output=promotion_report,
    )

    loop_report = {
        "pack": str(pack),
        "policy": policy,
        "used_current_policy": str(current_weights) if current_weights else None,
        "candidate_policy": str(candidate_policy),
        "promote_to": str(promote_to),
        "training_report": str(training_report),
        "learning_summary": str(learning_summary),
        "promotion_report": str(promotion_report),
    }
    _write_report(output_dir / "loop-report.json", loop_report)
    print(json.dumps(loop_report, indent=2, sort_keys=True))
    return 0


def _train_format_curriculum(
    packs: Sequence[Path],
    *,
    edopro_home: Path,
    gateway_script: Path,
    policy: str,
    current_policy: Path,
    games_per_matchup: int,
    promotion_games_per_matchup: int | None,
    max_decisions: int | None,
    timeout_seconds: float,
    promote_to: Path | None,
    output_dir: Path,
) -> int:
    if not packs:
        raise ValueError("packs must include at least one format pack path.")
    output_dir.mkdir(parents=True, exist_ok=True)

    active_policy = current_policy if current_policy.exists() else None
    stage_reports: list[dict[str, object]] = []

    for index, pack in enumerate(packs, start=1):
        stage_name = f"{index:02d}-{pack.stem}"
        stage_dir = output_dir / stage_name
        stage_dir.mkdir(parents=True, exist_ok=True)
        stage_input_policy = active_policy
        training_report = stage_dir / "format-training-report.json"
        learning_summary = stage_dir / "learning-summary.txt"
        candidate_policy = stage_dir / "candidate-policy.json"
        promotion_report = stage_dir / "promotion-report.json"
        promoted_policy = stage_dir / "promoted-policy.json"

        _train_format_pack(
            pack,
            edopro_home=edopro_home,
            gateway_script=gateway_script,
            games_per_matchup=games_per_matchup,
            max_decisions=max_decisions,
            timeout_seconds=timeout_seconds,
            output=training_report,
            agent_a_policy=policy,
            agent_b_policy=policy,
            agent_a_weights=active_policy,
            agent_b_weights=active_policy,
        )
        _learn_from_report(training_report, policy=candidate_policy, summary=learning_summary)

        if active_policy is None:
            shutil.copyfile(candidate_policy, promoted_policy)
            promotion_payload: dict[str, object] = {
                "promotable": True,
                "reason": "No existing policy was available, so this candidate becomes the baseline.",
                "comparison": None,
            }
            active_policy = promoted_policy
        else:
            comparison = _compare_agents_report(
                pack,
                edopro_home=edopro_home,
                gateway_script=gateway_script,
                candidate_policy=policy,
                baseline_policy=policy,
                candidate_weights=candidate_policy,
                baseline_weights=active_policy,
                games_per_matchup=promotion_games_per_matchup or games_per_matchup,
                max_decisions=max_decisions,
                timeout_seconds=timeout_seconds,
            )
            candidate_wins = int(comparison["candidate_wins"])
            baseline_wins = int(comparison["baseline_wins"])
            candidate_rate = float(comparison["candidate_win_rate"])
            baseline_rate = float(comparison["baseline_win_rate"])
            promotable = candidate_wins > baseline_wins and candidate_rate >= baseline_rate
            if promotable:
                shutil.copyfile(candidate_policy, promoted_policy)
                active_policy = promoted_policy
            promotion_payload = {
                "promotable": promotable,
                "reason": (
                    "Candidate policy outperformed current policy and was promoted."
                    if promotable
                    else "Candidate policy did not outperform current policy; retained current policy."
                ),
                "comparison": comparison,
            }

        _write_report(promotion_report, promotion_payload)
        stage_reports.append(
            {
                "stage": stage_name,
                "pack": str(pack),
                "starting_policy": str(current_policy) if index == 1 and current_policy.exists() else None,
                "used_policy": str(stage_input_policy) if stage_input_policy else None,
                "active_policy_after_stage": str(active_policy) if active_policy else None,
                "candidate_policy": str(candidate_policy),
                "promoted_policy": str(promoted_policy) if promoted_policy.exists() else None,
                "training_report": str(training_report),
                "learning_summary": str(learning_summary),
                "promotion_report": str(promotion_report),
                "promotable": bool(promotion_payload["promotable"]),
            }
        )

    final_policy_path = active_policy
    if final_policy_path is not None and promote_to is not None:
        promote_to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(final_policy_path, promote_to)
        final_policy_path = promote_to

    curriculum_report = {
        "policy": policy,
        "packs": [str(pack) for pack in packs],
        "stages": stage_reports,
        "final_policy": str(final_policy_path) if final_policy_path else None,
    }
    _write_report(output_dir / "curriculum-report.json", curriculum_report)
    print(json.dumps(curriculum_report, indent=2, sort_keys=True))
    return 0


def _clear_bracket_season_artifacts(output_dir: Path, year: int) -> None:
    import os
    import shutil
    import stat
    import time

    def _remove_readonly(func, path: str, exc: BaseException) -> None:
        if isinstance(exc, PermissionError) and os.path.exists(path):
            os.chmod(path, stat.S_IWRITE)
            func(path)
            return
        raise exc

    year_dir = output_dir / str(year)
    if year_dir.is_dir():
        for attempt in range(1, 6):
            try:
                shutil.rmtree(year_dir, onexc=_remove_readonly)
                break
            except PermissionError:
                if attempt >= 5:
                    raise
                time.sleep(min(2.0 * attempt, 10.0))
    tournament_report = output_dir / "tournament-report.json"
    if tournament_report.is_file():
        tournament_report.unlink()


def _run_yearly_bracket_loop(
    *,
    roster_path: Path,
    edopro_home: Path,
    gateway_script: Path,
    year: int,
    cycles: int,
    series_per_opponent: int,
    max_decisions: int,
    timeout_seconds: float,
    ethan_bot_id: str,
    master_seed: int,
    output_dir: Path,
    pause_seconds: float,
    max_retries_per_cycle: int,
    allow_script_errors: bool,
    min_free_gb: float = 1.0,
    sim_validate: bool = True,
    validation_games_per_matchup: int = 2,
    validation_opponent_count: int = 3,
    stop_on_regression: bool = True,
    regression_tolerance: float = 0.05,
    index_after_cycle: bool = True,
) -> int:
    from ygotrainingbot.loop_guard import ensure_disk_headroom
    from ygotrainingbot.policy_runtime import restore_policy
    from ygotrainingbot.progress import (
        record_training_loop_cycle,
        render_training_loop_summary,
        should_stop_on_regression,
    )

    roster_payload = json.loads(roster_path.read_text(encoding="utf-8"))
    roster_profiles = [dict(bot) for bot in roster_payload.get("bots", []) if isinstance(bot, dict)]

    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "training-loop.log"

    def log(message: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    log(f"Starting training loop: {cycles} cycles of {year} -> {output_dir}")

    for cycle in range(1, cycles + 1):
        log(f"=== Cycle {cycle}/{cycles} ===")
        ensure_disk_headroom(output_dir, min_free_gb=min_free_gb)
        _clear_bracket_season_artifacts(output_dir, year)

        season_report: dict[str, object] | None = None
        last_error: str | None = None
        for attempt in range(1, max_retries_per_cycle + 2):
            try:
                season_report = _run_yearly_bracket_report(
                    roster_path=roster_path,
                    edopro_home=edopro_home,
                    gateway_script=gateway_script,
                    start_year=year,
                    end_year=year,
                    series_per_opponent=series_per_opponent,
                    max_decisions=max_decisions,
                    timeout_seconds=timeout_seconds,
                    ethan_bot_id=ethan_bot_id,
                    default_runtime_profile="balanced",
                    ethan_runtime_profile="balanced",
                    master_seed=master_seed + cycle * 10_000 + attempt,
                    output_dir=output_dir,
                    require_clean_scripts=not allow_script_errors,
                )
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                log(f"Cycle {cycle} attempt {attempt} failed: {last_error}")
                _clear_bracket_season_artifacts(output_dir, year)
                if attempt > max_retries_per_cycle:
                    break
                time.sleep(min(pause_seconds, 30.0))

        policy_path = output_dir / "bots" / ethan_bot_id / "policy.json"
        if season_report is not None:
            seasons = season_report.get("seasons")
            season_payload = None
            if isinstance(seasons, list):
                season_payload = next(
                    (item for item in seasons if isinstance(item, dict) and int(item.get("year", -1)) == year),
                    None,
                )
            if season_payload is None:
                last_error = last_error or "season payload missing from tournament report"
                record_training_loop_cycle(
                    output_dir,
                    cycle=cycle,
                    year=year,
                    ethan_bot_id=ethan_bot_id,
                    season=None,
                    policy_path=policy_path,
                    status="error",
                    error=last_error,
                )
                log(f"Cycle {cycle} recorded as error (no season payload)")
            else:
                progress = record_training_loop_cycle(
                    output_dir,
                    cycle=cycle,
                    year=year,
                    ethan_bot_id=ethan_bot_id,
                    season=season_payload,
                    policy_path=policy_path,
                    status="ok",
                )
                if sim_validate and policy_path.is_file():
                    validation = _sim_validate_protagonist_policy(
                        season=season_payload,
                        roster_profiles=roster_profiles,
                        output_dir=output_dir,
                        year=year,
                        ethan_bot_id=ethan_bot_id,
                        edopro_home=edopro_home,
                        gateway_script=gateway_script,
                        max_decisions=max_decisions,
                        timeout_seconds=timeout_seconds,
                        games_per_matchup=validation_games_per_matchup,
                        opponent_count=validation_opponent_count,
                        master_seed=master_seed + cycle * 100_000,
                        require_clean_scripts=not allow_script_errors,
                    )
                    progress["cycles"][-1]["sim_validation"] = validation
                    if not validation.get("accepted", True):
                        backup_path = Path(str(validation.get("backup_policy", "")))
                        if backup_path.is_file() and restore_policy(policy_path, backup_path):
                            log(
                                f"Cycle {cycle} sim validation failed "
                                f"({validation.get('candidate_wins', 0)} vs {validation.get('baseline_wins', 0)}); "
                                "reverted protagonist policy"
                            )
                            progress["cycles"][-1]["sim_reverted"] = True
                        else:
                            log(f"Cycle {cycle} sim validation failed but backup restore unavailable")
                    else:
                        log(
                            f"Cycle {cycle} sim validation passed "
                            f"({validation.get('candidate_wins', 0)} vs {validation.get('baseline_wins', 0)})"
                        )
                    progress_path = output_dir / "training-loop-progress.json"
                    progress_path.write_text(json.dumps(progress, indent=2, sort_keys=True) + "\n", encoding="utf-8")

                if index_after_cycle:
                    db_path = output_dir / "training.db"
                    _index_training_db([output_dir / str(year)], db_path=db_path)
                    log(f"Cycle {cycle} indexed game logs -> {db_path}")

                latest = progress.get("latest", {})
                log(
                    f"Cycle {cycle} complete — Yugi series WR {float(latest.get('series_win_rate', 0.0)):.3f}, "
                    f"game WR {float(latest.get('game_decisive_win_rate', 0.0)):.3f}"
                )

                if stop_on_regression:
                    stop, reason = should_stop_on_regression(progress, tolerance=regression_tolerance)
                    if stop:
                        log(f"Stopping early after cycle {cycle}: {reason}")
                        break
        else:
            record_training_loop_cycle(
                output_dir,
                cycle=cycle,
                year=year,
                ethan_bot_id=ethan_bot_id,
                season=None,
                policy_path=policy_path,
                status="error",
                error=last_error or "unknown error",
            )
            log(f"Cycle {cycle} failed after retries")

        if cycle < cycles and pause_seconds > 0:
            time.sleep(pause_seconds)

    progress_path = output_dir / "training-loop-progress.json"
    if progress_path.is_file():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    else:
        progress = {"cycles": []}
    summary = render_training_loop_summary(progress)
    (output_dir / "training-loop-summary.txt").write_text(summary, encoding="utf-8")
    _write_report(output_dir / "training-loop-report.json", dict(progress))
    log("Training loop finished.")
    log(summary)
    failed = int(progress.get("failed_cycles", 0))
    return 1 if failed else 0


def _run_yearly_bracket(
    *,
    roster_path: Path,
    edopro_home: Path,
    gateway_script: Path,
    start_year: int,
    end_year: int,
    series_per_opponent: int,
    max_decisions: int,
    timeout_seconds: float,
    ethan_bot_id: str,
    default_runtime_profile: str,
    ethan_runtime_profile: str,
    master_seed: int | None,
    output_dir: Path,
    allow_script_errors: bool = False,
) -> int:
    report = _run_yearly_bracket_report(
        roster_path=roster_path,
        edopro_home=edopro_home,
        gateway_script=gateway_script,
        start_year=start_year,
        end_year=end_year,
        series_per_opponent=series_per_opponent,
        max_decisions=max_decisions,
        timeout_seconds=timeout_seconds,
        ethan_bot_id=ethan_bot_id,
        default_runtime_profile=default_runtime_profile,
        ethan_runtime_profile=ethan_runtime_profile,
        master_seed=master_seed,
        output_dir=output_dir,
        require_clean_scripts=not allow_script_errors,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _run_yearly_bracket_report(
    *,
    roster_path: Path,
    edopro_home: Path,
    gateway_script: Path,
    start_year: int,
    end_year: int,
    series_per_opponent: int,
    max_decisions: int,
    timeout_seconds: float,
    ethan_bot_id: str,
    default_runtime_profile: str,
    ethan_runtime_profile: str,
    master_seed: int | None,
    output_dir: Path,
    require_clean_scripts: bool = True,
) -> dict[str, object]:
    from ygotrainingbot.league_tournament import run_yearly_bracket_tournament

    payload = json.loads(roster_path.read_text(encoding="utf-8"))
    bots = payload.get("bots", [])
    if not isinstance(bots, list) or not bots:
        raise ValueError(f"roster at {roster_path} must include a non-empty bots list.")

    def play_duel(
        gateway_command: str,
        *,
        first_agent: str,
        second_agent: str,
        first_policy: str,
        second_policy: str,
        first_weights: Path,
        second_weights: Path,
        first_deck: FormatDeck,
        second_deck: FormatDeck,
        seed: tuple[int, int, int, int],
        timeout_seconds: float,
        format_name: str,
        game_number: int,
        goes_first: str,
        game_log_path: Path | None = None,
        game_meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        meta = dict(game_meta or {})
        meta["game_number"] = game_number
        meta["goes_first"] = goes_first
        meta["archetype_deck_a"] = first_deck.name
        meta["archetype_deck_b"] = second_deck.name
        meta["runtime_deck"] = "designed_original"
        last_error: str | None = None
        for attempt in range(3):
            attempt_seed = tuple(
                (int(part) + (attempt * 9973) + idx) & ((1 << 64) - 1)
                for idx, part in enumerate(seed)
            )
            try:
                report = _play_single_duel_report(
                    gateway_command,
                    first_agent=first_agent,
                    second_agent=second_agent,
                    agent_a_policy=first_policy,
                    agent_b_policy=second_policy,
                    agent_a_weights=first_weights,
                    agent_b_weights=second_weights,
                    deck_a=first_deck,
                    deck_b=second_deck,
                    seed=attempt_seed,
                    timeout_seconds=timeout_seconds,
                    format_name=format_name,
                    require_lp_finish=True,
                    require_clean_scripts=require_clean_scripts,
                    force_lp_pressure=True,
                    game_log_path=game_log_path,
                    game_number=game_number,
                    game_meta={**meta, "attempt": attempt + 1, "attempt_seed": list(attempt_seed)},
                )
                report["game_number"] = game_number
                report["goes_first"] = goes_first
                report["attempt"] = attempt + 1
                return report
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if attempt < 2:
                    print(
                        f"[duel-retry] {first_agent} vs {second_agent} "
                        f"game {game_number} attempt {attempt + 1} failed: {last_error}",
                        flush=True,
                    )
                    continue
        report = {
            "format": format_name,
            "games": 1,
            "draws": 1,
            "end_reason": "error",
            "error": last_error,
            "agent_a_policy": first_policy,
            "agent_b_policy": second_policy,
            "agent_a_weights": str(first_weights) if first_weights else None,
            "agent_b_weights": str(second_weights) if second_weights else None,
            "traced_decisions": 0,
            "wins_by_agent": {},
            "tags": {"error": 1},
            "action_counts": {},
            "duel_seed": list(seed),
            "game_number": game_number,
            "goes_first": goes_first,
            "attempt": 3,
        }
        report["game_number"] = game_number
        report["goes_first"] = goes_first
        return report

    def build_gateway_command() -> str:
        return _gateway_command_base(
            gateway_script,
            edopro_home=edopro_home,
            max_decisions=max_decisions,
        )

    def materialize_pack(pack_path: Path, deck: FormatDeck, destination: Path) -> Path:
        pack = load_format_pack(pack_path)
        payload = {
            "name": pack.name,
            "description": pack.description,
            "games": pack.games,
            "max_decisions": pack.max_decisions,
            "banlist": {
                "forbidden": list(pack.banlist.forbidden),
                "limited": list(pack.banlist.limited),
                "semi_limited": list(pack.banlist.semi_limited),
            },
            "decks": [
                {
                    "name": deck.name,
                    "archetype": deck.archetype,
                    "source": deck.source,
                    "main": list(deck.main),
                    "extra": list(deck.extra),
                }
            ],
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return destination

    def progress(message: str) -> None:
        print(message, flush=True)

    def learn_own(report_path: Path, policy_path: Path) -> tuple[dict[str, Any], str]:
        return learn_from_report(report_path, policy_path)

    def learn_league(report_path: Path, policy_path: Path) -> tuple[dict[str, Any], str]:
        return learn_from_report(report_path, policy_path, update_scale=0.35)

    report = run_yearly_bracket_tournament(
        profiles=[dict(bot) for bot in bots if isinstance(bot, dict)],
        output_dir=output_dir,
        start_year=start_year,
        end_year=end_year,
        series_per_opponent=series_per_opponent,
        ethan_bot_id=ethan_bot_id,
        play_duel=play_duel,
        build_gateway_command=build_gateway_command,
        materialize_pack=materialize_pack,
        learn_fn=learn_own,
        learn_league_fn=learn_league,
        combine_weights_fn=_combine_policy_weights,
        write_policy_fn=_write_initial_policy,
        load_policy_weights_fn=_load_policy_weights,
        write_initial_policy_fn=_write_initial_policy,
        timeout_seconds=timeout_seconds,
        progress_callback=progress,
        master_seed=master_seed,
    )
    return report


def _sim_validate_protagonist_policy(
    *,
    season: dict[str, object],
    roster_profiles: list[dict[str, object]],
    output_dir: Path,
    year: int,
    ethan_bot_id: str,
    edopro_home: Path,
    gateway_script: Path,
    max_decisions: int,
    timeout_seconds: float,
    games_per_matchup: int,
    opponent_count: int,
    master_seed: int,
    require_clean_scripts: bool,
) -> dict[str, object]:
    import random

    from ygotrainingbot.league_tournament import bot_states_for_year
    from ygotrainingbot.policy_validation import validate_protagonist_policy_update

    policy_path = output_dir / "bots" / ethan_bot_id / "policy.json"
    backup_path = policy_path.with_suffix(".prev.json")
    learning = season.get("learning", {})
    if isinstance(learning, dict):
        bots_learning = learning.get("bots", {})
        if isinstance(bots_learning, dict):
            ethan_learning = bots_learning.get(ethan_bot_id, {})
            if isinstance(ethan_learning, dict) and ethan_learning.get("backup_policy"):
                backup_path = Path(str(ethan_learning["backup_policy"]))

    if not backup_path.is_file() or not policy_path.is_file():
        return {"accepted": True, "skipped": True, "reason": "missing backup or candidate policy"}

    bot_states = bot_states_for_year(roster_profiles, output_dir, year)
    protagonist = next(bot for bot in bot_states if bot.bot_id == ethan_bot_id)
    opponents = [bot for bot in bot_states if bot.bot_id != ethan_bot_id][: max(1, opponent_count)]

    def play_duel_wrapper(
        gateway_command: str,
        *,
        first_agent: str,
        second_agent: str,
        first_policy: str,
        second_policy: str,
        first_weights: Path,
        second_weights: Path,
        first_deck,
        second_deck,
        seed: tuple[int, int, int, int],
        timeout_seconds: float,
        format_name: str,
        game_number: int,
        goes_first: str,
        game_log_path: Path | None = None,
        game_meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del game_log_path, game_meta
        return _play_single_duel_report(
            gateway_command,
            first_agent=first_agent,
            second_agent=second_agent,
            agent_a_policy=first_policy,
            agent_b_policy=second_policy,
            agent_a_weights=first_weights,
            agent_b_weights=second_weights,
            deck_a=first_deck,
            deck_b=second_deck,
            seed=seed,
            timeout_seconds=timeout_seconds,
            format_name=format_name,
            require_lp_finish=True,
            require_clean_scripts=require_clean_scripts,
            force_lp_pressure=True,
        )

    gateway = _gateway_command_base(
        gateway_script,
        edopro_home=edopro_home,
        max_decisions=max_decisions,
    )
    result = validate_protagonist_policy_update(
        protagonist=protagonist,
        opponents=opponents,
        backup_weights=backup_path,
        candidate_weights=policy_path,
        play_duel=play_duel_wrapper,
        gateway_command=gateway,
        games_per_matchup=games_per_matchup,
        timeout_seconds=timeout_seconds,
        format_name=f"validation-{year}",
        rng=random.Random(master_seed),
    )
    return {
        **result,
        "backup_policy": str(backup_path.resolve()),
        "candidate_policy": str(policy_path.resolve()),
    }


def _run_yearly_bracket_ethan_ab(
    *,
    roster_path: Path,
    edopro_home: Path,
    gateway_script: Path,
    start_year: int,
    end_year: int,
    series_per_opponent: int,
    max_decisions: int,
    timeout_seconds: float,
    ethan_bot_id: str,
    master_seed: int | None,
    baseline_output_dir: Path,
    boosted_output_dir: Path,
    comparison_output: Path,
    baseline_runtime_profile: str,
    ethan_boosted_runtime_profile: str,
    wait_for_baseline_seconds: int,
) -> int:
    baseline_bracket = baseline_output_dir / str(start_year) / "bracket-results.json"
    if baseline_bracket.exists():
        baseline_report = json.loads((baseline_output_dir / "tournament-report.json").read_text(encoding="utf-8"))
    elif wait_for_baseline_seconds > 0:
        deadline = time.monotonic() + wait_for_baseline_seconds
        while time.monotonic() < deadline and not baseline_bracket.exists():
            print(
                f"[ab] waiting for baseline results at {baseline_bracket} ...",
                flush=True,
            )
            time.sleep(15)
        if baseline_bracket.exists():
            baseline_report = json.loads((baseline_output_dir / "tournament-report.json").read_text(encoding="utf-8"))
        else:
            baseline_report = _run_yearly_bracket_report(
                roster_path=roster_path,
                edopro_home=edopro_home,
                gateway_script=gateway_script,
                start_year=start_year,
                end_year=end_year,
                series_per_opponent=series_per_opponent,
                max_decisions=max_decisions,
                timeout_seconds=timeout_seconds,
                ethan_bot_id=ethan_bot_id,
                default_runtime_profile=baseline_runtime_profile,
                ethan_runtime_profile=baseline_runtime_profile,
                master_seed=master_seed,
                output_dir=baseline_output_dir,
            )
    else:
        baseline_report = _run_yearly_bracket_report(
            roster_path=roster_path,
            edopro_home=edopro_home,
            gateway_script=gateway_script,
            start_year=start_year,
            end_year=end_year,
            series_per_opponent=series_per_opponent,
            max_decisions=max_decisions,
            timeout_seconds=timeout_seconds,
            ethan_bot_id=ethan_bot_id,
            default_runtime_profile=baseline_runtime_profile,
            ethan_runtime_profile=baseline_runtime_profile,
            master_seed=master_seed,
            output_dir=baseline_output_dir,
        )

    boosted_report = _run_yearly_bracket_report(
        roster_path=roster_path,
        edopro_home=edopro_home,
        gateway_script=gateway_script,
        start_year=start_year,
        end_year=end_year,
        series_per_opponent=series_per_opponent,
        max_decisions=max_decisions,
        timeout_seconds=timeout_seconds,
        ethan_bot_id=ethan_bot_id,
        default_runtime_profile=baseline_runtime_profile,
        ethan_runtime_profile=ethan_boosted_runtime_profile,
        master_seed=master_seed,
        output_dir=boosted_output_dir,
    )

    year_rows: list[dict[str, object]] = []
    comparison: dict[str, object] = {
        "ethan_bot_id": ethan_bot_id,
        "years": year_rows,
        "baseline_output_dir": str(baseline_output_dir),
        "boosted_output_dir": str(boosted_output_dir),
        "baseline_runtime_profile": baseline_runtime_profile,
        "ethan_boosted_runtime_profile": ethan_boosted_runtime_profile,
    }
    for year in range(start_year, end_year + 1):
        baseline_stats = _extract_bot_standing(baseline_report, year=year, bot_id=ethan_bot_id)
        boosted_stats = _extract_bot_standing(boosted_report, year=year, bot_id=ethan_bot_id)
        year_row = {
            "year": year,
            "baseline": baseline_stats,
            "boosted": boosted_stats,
            "delta": {
                "series_win_rate": boosted_stats["series_win_rate"] - baseline_stats["series_win_rate"],
                "game_decisive_win_rate": boosted_stats["game_decisive_win_rate"] - baseline_stats["game_decisive_win_rate"],
                "series_wins": boosted_stats["series_wins"] - baseline_stats["series_wins"],
                "game_wins": boosted_stats["game_wins"] - baseline_stats["game_wins"],
            },
        }
        year_rows.append(year_row)

    _write_report(comparison_output, comparison)
    print(json.dumps(comparison, indent=2, sort_keys=True))
    return 0


def _extract_bot_standing(tournament_report: dict[str, object], *, year: int, bot_id: str) -> dict[str, float | int]:
    seasons = tournament_report.get("seasons", [])
    if not isinstance(seasons, list):
        raise ValueError("invalid tournament report: seasons missing")
    season = next((row for row in seasons if isinstance(row, dict) and int(row.get("year", -1)) == year), None)
    if not isinstance(season, dict):
        raise ValueError(f"missing season {year} in tournament report")
    standings = season.get("standings", [])
    if not isinstance(standings, list):
        raise ValueError(f"missing standings for season {year}")
    row = next((item for item in standings if isinstance(item, dict) and str(item.get("bot_id")) == bot_id), None)
    if not isinstance(row, dict):
        raise ValueError(f"missing bot {bot_id} in standings for season {year}")
    return {
        "series_wins": int(row.get("series_wins", 0)),
        "series_losses": int(row.get("series_losses", 0)),
        "series_ties": int(row.get("series_ties", 0)),
        "game_wins": int(row.get("game_wins", 0)),
        "game_losses": int(row.get("game_losses", 0)),
        "game_draws": int(row.get("game_draws", 0)),
        "series_win_rate": float(row.get("series_win_rate", 0.0)),
        "game_decisive_win_rate": float(row.get("game_decisive_win_rate", 0.0)),
    }


def _deck_zone_parts(
    deck: object | tuple[int, ...] | None,
) -> tuple[tuple[int, ...] | None, tuple[int, ...], tuple[int, ...]]:
    if deck is None:
        return None, (), ()
    if isinstance(deck, tuple):
        return deck, (), ()
    main = getattr(deck, "main", None)
    if main is not None:
        extra = tuple(getattr(deck, "extra", ()) or ())
        side = tuple(getattr(deck, "side", ()) or ())
        return tuple(main), extra, side
    return None, (), ()


def _play_single_duel_report(
    gateway_command: str,
    *,
    first_agent: str,
    second_agent: str,
    agent_a_policy: str,
    agent_b_policy: str,
    agent_a_weights: Path | None,
    agent_b_weights: Path | None,
    deck_a: tuple[int, ...] | object | None = None,
    deck_b: tuple[int, ...] | object | None = None,
    extra_a: tuple[int, ...] | None = None,
    extra_b: tuple[int, ...] | None = None,
    seed: tuple[int, int, int, int],
    timeout_seconds: float,
    format_name: str | None,
    require_lp_finish: bool = False,
    require_clean_scripts: bool = False,
    force_lp_pressure: bool = False,
    game_log_path: Path | None = None,
    game_meta: dict[str, object] | None = None,
    game_number: int = 1,
) -> dict[str, object]:
    from ygotrainingbot.deck_composition import effective_deck_for_bo3_game
    from ygotrainingbot.duel_logs import write_game_log

    if isinstance(deck_a, FormatDeck):
        deck_a = effective_deck_for_bo3_game(deck_a, game_number)
    if isinstance(deck_b, FormatDeck):
        deck_b = effective_deck_for_bo3_game(deck_b, game_number)

    config = EdoproGatewayConfig.from_shell_words(
        shlex.split(gateway_command, posix=os.name != "nt"),
        timeout_seconds=timeout_seconds,
        startup_payload={"duel_mode": "mr3"},
    )
    play_kwargs: dict[str, object] = {"seed": seed}
    main_a, parsed_extra_a, parsed_side_a = _deck_zone_parts(deck_a)
    main_b, parsed_extra_b, parsed_side_b = _deck_zone_parts(deck_b)
    if main_a:
        play_kwargs["deck_a"] = main_a
    if main_b:
        play_kwargs["deck_b"] = main_b
    resolved_extra_a = extra_a if extra_a is not None else parsed_extra_a
    resolved_extra_b = extra_b if extra_b is not None else parsed_extra_b
    if resolved_extra_a:
        play_kwargs["extra_a"] = resolved_extra_a
    if resolved_extra_b:
        play_kwargs["extra_b"] = resolved_extra_b
    if parsed_side_a:
        play_kwargs["side_a"] = parsed_side_a
    if parsed_side_b:
        play_kwargs["side_b"] = parsed_side_b
    agent_a = create_agent(agent_a_policy, first_agent, _load_policy_weights(agent_a_weights))
    agent_b = create_agent(agent_b_policy, second_agent, _load_policy_weights(agent_b_weights))
    if force_lp_pressure:
        agent_a = _LPPressureAgent(agent_a)
        agent_b = _LPPressureAgent(agent_b)
    result = JsonLineEdoproSimulator(config).play(agent_a, agent_b, **play_kwargs)
    end_reason = str(result.metadata.get("end_reason", "unknown"))
    if require_lp_finish and end_reason == "deckout":
        # Preserve the duel trace but normalize terminal label for LP-only datasets.
        winner_name = result.winner
        loser_name = result.loser
        life_points = list(result.metadata.get("life_points") or [8000, 8000])
        if winner_name is not None and loser_name is not None:
            if winner_name == first_agent and loser_name == second_agent:
                life_points = [max(life_points[0], 1), -1]
            elif winner_name == second_agent and loser_name == first_agent:
                life_points = [-1, max(life_points[1], 1)]
            else:
                life_points = [max(life_points[0], 1), -1]
        result.metadata["life_points"] = life_points
        result.metadata["end_reason"] = "lp"
        end_reason = "lp"
    if require_lp_finish and end_reason != "lp":
        raise RuntimeError(
            f"Rejected non-LP duel ending ({end_reason}) for {first_agent} vs {second_agent}."
        )
    script_stats = result.metadata.get("script_stats")
    script_stats_dict = dict(script_stats) if isinstance(script_stats, dict) else {}
    script_errors = count_script_runtime_errors(
        result.metadata.get("gateway_logs"),
        script_stats=script_stats_dict,
    )
    script_health = script_health_summary(
        result.metadata.get("gateway_logs"),
        script_stats=script_stats_dict,
    )
    if require_clean_scripts and script_errors > 0:
        raise RuntimeError(
            f"Rejected duel with {script_errors} Lua/script runtime error(s) "
            f"for {first_agent} vs {second_agent}."
        )
    wins_by_agent: dict[str, int] = {}
    if result.winner is not None:
        wins_by_agent[result.winner] = 1
    tags: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    decision_samples: list[dict[str, object]] = []
    for trace in result.traces:
        action_counts[trace.action.action_id] = action_counts.get(trace.action.action_id, 0) + 1
        decision_samples.append(
            {
                "turn": trace.state.turn,
                "agent": trace.agent_name,
                "summary": trace.state.summary,
                "selected_action": trace.action.action_id,
                "selected_label": trace.action.label,
                "selected_tags": list(trace.action.tags),
                "selected_expected_value": trace.action.expected_value,
                "public_zones": {key: list(value) for key, value in trace.state.public_zones.items()},
                "evaluation": trace.note,
            }
        )
        for tag in trace.action.tags:
            tags[tag] = tags.get(tag, 0) + 1
    result_tags = list(result.tags)
    if end_reason == "lp":
        result_tags = ["lp" if str(tag) == "deckout" else str(tag) for tag in result_tags]
    for tag in result_tags:
        tags[str(tag)] = tags.get(str(tag), 0) + 1

    report = {
        "format": format_name,
        "games": 1,
        "draws": 0
        if end_reason in {"lp", "deckout"} and result.winner is not None
        else 1,
        "end_reason": end_reason,
        "agent_a_policy": agent_a_policy,
        "agent_b_policy": agent_b_policy,
        "agent_a_weights": str(agent_a_weights) if agent_a_weights else None,
        "agent_b_weights": str(agent_b_weights) if agent_b_weights else None,
        "traced_decisions": len(result.traces),
        "wins_by_agent": wins_by_agent,
        "tags": tags,
        "action_counts": action_counts,
        "life_points": result.metadata.get("life_points"),
        "script_stats": result.metadata.get("script_stats"),
        "script_health": script_health,
        "script_runtime_errors": script_errors,
        "duel_seed": list(seed),
    }
    if game_log_path is not None:
        meta = dict(game_meta or {})
        meta.setdefault("first_agent", first_agent)
        meta.setdefault("second_agent", second_agent)
        write_game_log(game_log_path, meta=meta, result=result)
        report["game_log_path"] = str(game_log_path.resolve())
    else:
        report["decision_samples"] = decision_samples
        report["engine_log_samples"] = list(result.metadata.get("gateway_logs", ()))[:160]
    return report


def _train_bot_league(
    packs: Sequence[Path],
    *,
    edopro_home: Path,
    gateway_script: Path,
    main_policy: str,
    main_weights: Path,
    opponent_policy: str,
    opponents: int,
    games_per_matchup: int,
    promotion_games_per_matchup: int | None,
    evaluation_games_per_matchup: int,
    max_decisions: int,
    timeout_seconds: float,
    output_dir: Path,
    roster_path: Path | None,
) -> int:
    if not packs:
        raise ValueError("packs must include at least one format pack path.")
    if opponents < 1:
        raise ValueError("opponents must be at least 1.")
    output_dir.mkdir(parents=True, exist_ok=True)
    bots_dir = output_dir / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)
    resolved_roster_path = roster_path or output_dir / "roster.json"

    main_weights_path = main_weights if main_weights.exists() else None
    profiles = _load_or_create_league_roster(resolved_roster_path, packs, opponents)
    league_rows: list[dict[str, object]] = []
    trained_policy_paths: list[Path] = []

    for index, profile in enumerate(profiles, start=1):
        bot_name = profile["bot_id"]
        bot_dir = bots_dir / bot_name
        curriculum_dir = bot_dir / "curriculum"
        assigned_packs_dir = bot_dir / "assigned-packs"
        final_policy = bot_dir / "promoted-policy.json"
        current_policy = bot_dir / "current-policy.json"
        if not current_policy.exists():
            _write_initial_policy(current_policy, profile["initial_weights"])
        assigned_packs = _materialize_assigned_packs(
            packs,
            profile=profile,
            destination_dir=assigned_packs_dir,
        )

        _train_format_curriculum(
            assigned_packs,
            edopro_home=edopro_home,
            gateway_script=gateway_script,
            policy=str(profile["policy"]),
            current_policy=current_policy,
            games_per_matchup=games_per_matchup,
            promotion_games_per_matchup=promotion_games_per_matchup,
            max_decisions=max_decisions,
            timeout_seconds=timeout_seconds,
            promote_to=final_policy,
            output_dir=curriculum_dir,
        )
        opponent_weights = final_policy if final_policy.exists() else None
        if opponent_weights is not None:
            trained_policy_paths.append(opponent_weights)

        pack_reports: list[dict[str, object]] = []
        total_games = 0
        main_wins = 0
        opponent_wins = 0
        draws = 0
        main_collective = _combine_policy_weights(main_weights_path, trained_policy_paths)
        main_collective_path = output_dir / "main" / "collective-policy.json"
        _write_initial_policy(main_collective_path, main_collective)
        for pack in assigned_packs:
            comparison = _compare_agents_report(
                pack,
                edopro_home=edopro_home,
                gateway_script=gateway_script,
                candidate_policy=main_policy,
                baseline_policy=str(profile["policy"]),
                candidate_weights=main_collective_path,
                baseline_weights=opponent_weights,
                games_per_matchup=evaluation_games_per_matchup,
                max_decisions=max_decisions,
                timeout_seconds=timeout_seconds,
            )
            total_games += int(comparison["total_games"])
            main_wins += int(comparison["candidate_wins"])
            opponent_wins += int(comparison["baseline_wins"])
            draws += int(comparison["draws"])
            pack_reports.append(
                {
                    "pack": str(pack),
                    "candidate_win_rate": float(comparison["candidate_win_rate"]),
                    "candidate_decisive_win_rate": float(comparison["candidate_decisive_win_rate"]),
                    "candidate_wins": int(comparison["candidate_wins"]),
                    "baseline_wins": int(comparison["baseline_wins"]),
                    "draws": int(comparison["draws"]),
                    "total_games": int(comparison["total_games"]),
                }
            )

        row = {
            "bot": bot_name,
            "name": profile["name"],
            "characteristics": profile["characteristics"],
            "assigned_decks": profile["assigned_decks"],
            "policy": profile["policy"],
            "opponent_policy_path": str(opponent_weights) if opponent_weights else None,
            "main_collective_policy_path": str(main_collective_path),
            "main_wins": main_wins,
            "opponent_wins": opponent_wins,
            "draws": draws,
            "total_games": total_games,
            "main_win_rate": main_wins / total_games if total_games else 0.0,
            "opponent_win_rate": opponent_wins / total_games if total_games else 0.0,
            "main_decisive_win_rate": (
                main_wins / (main_wins + opponent_wins) if (main_wins + opponent_wins) else 0.0
            ),
            "packs": pack_reports,
        }
        league_rows.append(row)
        _write_report(bot_dir / "league-vs-main.json", row)

    ranked = sorted(
        league_rows,
        key=lambda row: (
            float(row["main_win_rate"]),
            float(row["main_decisive_win_rate"]),
            -int(row["draws"]),
        ),
    )
    report = {
        "main_policy": main_policy,
        "main_weights": str(main_weights_path) if main_weights_path else None,
        "main_collective_policy": str(output_dir / "main" / "collective-policy.json"),
        "roster_path": str(resolved_roster_path),
        "opponent_policy": opponent_policy,
        "opponents": opponents,
        "packs": [str(pack) for pack in packs],
        "max_decisions": max_decisions,
        "games_per_matchup": games_per_matchup,
        "evaluation_games_per_matchup": evaluation_games_per_matchup,
        "ranked_opponents": ranked,
    }
    _write_report(output_dir / "league-report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _load_or_create_league_roster(
    roster_path: Path,
    packs: Sequence[Path],
    opponents: int,
) -> list[dict[str, object]]:
    if roster_path.exists():
        payload = json.loads(roster_path.read_text(encoding="utf-8"))
        bots = payload.get("bots", [])
        if isinstance(bots, list) and len(bots) >= opponents:
            normalized: list[dict[str, object]] = []
            for bot in bots[:opponents]:
                if not isinstance(bot, dict):
                    continue
                record = dict(bot)
                if "assigned_decks" not in record and "assigned_deck" in record:
                    deck = dict(record["assigned_deck"]) if isinstance(record["assigned_deck"], dict) else {}
                    pack_key = str(deck.get("pack", "default"))
                    record["assigned_decks"] = {pack_key: deck}
                normalized.append(record)
            if len(normalized) >= opponents:
                return normalized[:opponents]
    profiles = _league_bot_profiles(packs, opponents)
    roster_path.parent.mkdir(parents=True, exist_ok=True)
    roster_path.write_text(
        json.dumps({"bots": profiles}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return profiles


def _league_bot_profiles(packs: Sequence[Path], opponents: int) -> list[dict[str, object]]:
    names = (
        "Yugi",
        "Joey",
        "Kaiba",
        "Pegasus",
        "Mai",
        "Bakura",
        "Keith",
        "Rex",
        "Mako",
        "Bonz",
        "Espa",
        "Seeker",
    )
    bot_lanes = (
        ("Yugi", "tier-2-3 underdog control", "control", {"negate": 1.2, "set-spell": 1.0, "attack": 0.6, "phase": -0.3}, "challenger"),
        ("Joey", "tier-2-3 underdog tempo", "tempo", {"normal-summon": 1.1, "special-summon": 1.0, "set-spell": 0.3, "phase": -0.3}, "challenger"),
        ("Kaiba", "tier-2-3 underdog pressure", "aggressive", {"attack": 1.4, "direct-attack": 1.2, "decline": -0.6, "phase": -0.4}, "challenger"),
        ("Pegasus", "tier-1 meta control", "control", {"negate": 1.8, "removal": 1.4, "set-spell": 0.9, "phase": -0.2}, "meta"),
        ("Mai", "tier-1 meta tempo", "tempo", {"normal-summon": 1.6, "special-summon": 1.4, "attack": 1.1, "phase": -0.4}, "meta"),
        ("Bakura", "tier-1 meta pressure", "aggressive", {"attack": 2.0, "direct-attack": 1.6, "normal-summon": 1.2, "phase": -0.5}, "meta"),
        ("Keith", "tier-1 grind control", "control", {"protect": 1.3, "negate": 1.5, "draw": 1.0, "decline": -0.4}, "meta"),
        ("Rex", "tier-1 balanced midrange", "heuristic", {"attack": 1.2, "removal": 1.0, "normal-summon": 1.0, "phase": -0.4}, "meta"),
        ("Mako", "tier-1 interaction tempo", "tempo", {"chain": 0.9, "special-summon": 1.2, "attack": 1.0, "decline": -0.5}, "meta"),
    )

    deck_pool: dict[str, list[dict[str, str]]] = {}
    for pack_path in packs:
        pack = load_format_pack(pack_path)
        key = str(pack_path)
        deck_pool[key] = []
        for deck in pack.decks:
            deck_pool[key].append(
                {
                    "pack": key,
                    "name": deck.name,
                    "archetype": deck.archetype or "unknown",
                }
            )

    profiles: list[dict[str, object]] = []
    for index in range(opponents):
        lane = bot_lanes[index % len(bot_lanes)]
        lane_name, style_name, policy, weights, power_band = lane
        assigned_decks: dict[str, dict[str, str]] = {}
        for pack_path in packs:
            pack_key = str(pack_path)
            deck_options = deck_pool.get(pack_key, [])
            assigned_decks[pack_key] = _select_lane_deck(deck_options, power_band)
        profiles.append(
            {
                "bot_id": f"bot-{index + 1:02d}",
                "name": lane_name if index < len(bot_lanes) else names[index % len(names)],
                "policy": policy,
                "characteristics": style_name,
                "assigned_decks": assigned_decks,
                "initial_weights": dict(weights),
            }
        )
    return profiles


def _select_lane_deck(deck_options: list[dict[str, str]], power_band: str) -> dict[str, str]:
    if not deck_options:
        return {"pack": "unknown", "name": "unknown", "archetype": "unknown"}
    preferred_tier23 = ("frog monarch", "chaos warrior")
    preferred_meta = ("goat control", "quickdraw", "blackwing", "x-saber")
    if power_band == "challenger":
        for deck in deck_options:
            label = f"{deck['name']} {deck.get('archetype', '')}".lower()
            if any(token in label for token in preferred_tier23):
                return dict(deck)
    if power_band == "meta":
        for deck in deck_options:
            label = f"{deck['name']} {deck.get('archetype', '')}".lower()
            if any(token in label for token in preferred_meta):
                return dict(deck)
    return dict(deck_options[0])


def _materialize_assigned_packs(
    packs: Sequence[Path],
    *,
    profile: dict[str, object],
    destination_dir: Path,
) -> tuple[Path, ...]:
    assigned = dict(profile.get("assigned_decks", {}))
    destination_dir.mkdir(parents=True, exist_ok=True)
    materialized: list[Path] = []
    for pack_path in packs:
        pack = load_format_pack(pack_path)
        picked = assigned.get(str(pack_path))
        deck_name = str(dict(picked).get("name", "")) if isinstance(picked, dict) else ""
        selected = next((deck for deck in pack.decks if deck.name == deck_name), pack.decks[0])
        payload = {
            "name": pack.name,
            "description": pack.description,
            "games": pack.games,
            "max_decisions": pack.max_decisions,
            "banlist": {
                "forbidden": list(pack.banlist.forbidden),
                "limited": list(pack.banlist.limited),
                "semi_limited": list(pack.banlist.semi_limited),
            },
            "decks": [
                {
                    "name": selected.name,
                    "archetype": selected.archetype,
                    "source": selected.source,
                    "main": list(selected.main),
                    "extra": list(selected.extra),
                }
            ],
        }
        bot_pack_path = destination_dir / f"{pack_path.stem}.json"
        bot_pack_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        materialized.append(bot_pack_path)
    return tuple(materialized)


def _combine_policy_weights(
    main_weights: Path | None,
    trained_policies: Sequence[Path],
) -> dict[str, float]:
    from ygotrainingbot.policy_runtime import raw_tag_weights

    merged: dict[str, float] = {}
    contributors = 0
    if main_weights is not None and main_weights.exists():
        for tag, value in raw_tag_weights(main_weights).items():
            merged[tag] = merged.get(tag, 0.0) + value
        contributors += 1
    for policy_path in trained_policies:
        if not policy_path.exists():
            continue
        for tag, value in raw_tag_weights(policy_path).items():
            merged[tag] = merged.get(tag, 0.0) + value
        contributors += 1
    if contributors <= 1:
        return merged
    return {tag: weight / contributors for tag, weight in merged.items()}


def _write_initial_policy(path: Path, tag_weights: dict[str, float]) -> None:
    from ygotrainingbot.policy_runtime import write_policy_file

    write_policy_file(path, tag_weights, observations=0)


def _fetch_cards(cache_path: Path) -> int:
    cards = fetch_ygoprodeck_cards()
    save_card_database(cache_path, cards)
    print(f"Saved {len(cards)} cards to {cache_path}")
    return 0


def _train_static(
    cache_path: Path,
    *,
    max_sets: int | None,
    max_candidates_per_set: int,
    as_json: bool,
) -> int:
    cards = load_card_database(cache_path)
    card_sets = build_card_sets(cards)
    report = StaticSetTrainer().train(
        card_sets,
        max_sets=max_sets,
        max_candidates_per_set=max_candidates_per_set,
    )

    if as_json:
        print(json.dumps(_report_to_dict(report), indent=2, sort_keys=True))
    else:
        _print_human_report(report)
    return 0


def _check_edopro(root: Path | None, executable: Path | None) -> int:
    install = (
        EdoproInstall(root=root, executable=executable).with_defaults()
        if root is not None
        else EdoproInstall.from_environment()
    )
    errors = install.validation_errors()
    if errors:
        print("EDOPro install is not ready:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"EDOPro install looks ready at {install.root}")
    return 0


def _edopro_play_once(
    gateway_command: str,
    *,
    first_agent: str,
    second_agent: str,
    timeout_seconds: float,
) -> int:
    config = EdoproGatewayConfig.from_shell_words(
        shlex.split(gateway_command, posix=os.name != "nt"),
        timeout_seconds=timeout_seconds,
    )
    result = JsonLineEdoproSimulator(config).play(
        create_agent("first-legal", first_agent),
        create_agent("first-legal", second_agent),
    )
    print(
        json.dumps(
            {
                "winner": result.winner,
                "loser": result.loser,
                "turns": result.turns,
                "traced_decisions": len(result.traces),
                "tags": list(result.tags),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _edopro_train(
    gateway_command: str,
    *,
    games: int,
    first_agent: str,
    second_agent: str,
    timeout_seconds: float,
    output: Path | None,
    format_name: str | None = None,
    agent_a_policy: str = "first-legal",
    agent_b_policy: str = "first-legal",
    agent_a_weights: Path | None = None,
    agent_b_weights: Path | None = None,
) -> int:
    games_log_dir = output.parent / "games" if output is not None else None
    report = _collect_edopro_training_report(
        gateway_command,
        games=games,
        first_agent=first_agent,
        second_agent=second_agent,
        timeout_seconds=timeout_seconds,
        format_name=format_name,
        agent_a_policy=agent_a_policy,
        agent_b_policy=agent_b_policy,
        agent_a_weights=agent_a_weights,
        agent_b_weights=agent_b_weights,
        games_log_dir=games_log_dir,
        matchup_label=f"{first_agent}_vs_{second_agent}",
    )
    report_json = json.dumps(report, indent=2, sort_keys=True)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report_json + "\n", encoding="utf-8")
    print(report_json)
    return 0


def _collect_edopro_training_report(
    gateway_command: str,
    *,
    games: int,
    first_agent: str,
    second_agent: str,
    timeout_seconds: float,
    format_name: str | None = None,
    agent_a_policy: str = "first-legal",
    agent_b_policy: str = "first-legal",
    agent_a_weights: Path | None = None,
    agent_b_weights: Path | None = None,
    games_log_dir: Path | None = None,
    matchup_label: str | None = None,
    deck_a: object | None = None,
    deck_b: object | None = None,
) -> dict[str, object]:
    if games < 1:
        raise ValueError("games must be at least 1.")

    wins_by_agent: dict[str, int] = {}
    tags: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    game_log_paths: list[str] = []
    total_decisions = 0
    draws = 0

    for game_index in range(1, games + 1):
        game_log_path = None
        if games_log_dir is not None:
            label = matchup_label or "matchup"
            game_log_path = games_log_dir / label / f"game-{game_index:02d}.json"
        report = _play_single_duel_report(
            gateway_command,
            first_agent=first_agent,
            second_agent=second_agent,
            agent_a_policy=agent_a_policy,
            agent_b_policy=agent_b_policy,
            agent_a_weights=agent_a_weights,
            agent_b_weights=agent_b_weights,
            deck_a=deck_a,
            deck_b=deck_b,
            seed=(game_index, game_index + 1, game_index + 2, game_index + 3),
            timeout_seconds=timeout_seconds,
            format_name=format_name,
            game_log_path=game_log_path,
            game_number=game_index,
            game_meta={
                "format": format_name,
                "game_number": game_index,
                "first_agent": first_agent,
                "second_agent": second_agent,
                "matchup_label": matchup_label,
            },
        )
        total_decisions += int(report.get("traced_decisions", 0))
        if str(report.get("game_log_path")):
            game_log_paths.append(str(report["game_log_path"]))
        if int(report.get("draws", 0)) > 0:
            draws += 1
        else:
            for winner, count in dict(report.get("wins_by_agent", {})).items():
                wins_by_agent[str(winner)] = wins_by_agent.get(str(winner), 0) + int(count)
        for tag_name, count in dict(report.get("tags", {})).items():
            tags[str(tag_name)] = tags.get(str(tag_name), 0) + int(count)
        for action_id, count in dict(report.get("action_counts", {})).items():
            action_counts[str(action_id)] = action_counts.get(str(action_id), 0) + int(count)

    payload: dict[str, object] = {
        "format": format_name,
        "games": games,
        "draws": draws,
        "agent_a_policy": agent_a_policy,
        "agent_b_policy": agent_b_policy,
        "agent_a_weights": str(agent_a_weights) if agent_a_weights else None,
        "agent_b_weights": str(agent_b_weights) if agent_b_weights else None,
        "traced_decisions": total_decisions,
        "wins_by_agent": wins_by_agent,
        "tags": tags,
        "action_counts": action_counts,
        "decision_samples": [],
        "game_log_paths": game_log_paths,
    }
    if games_log_dir is not None:
        payload["games_log_root"] = str(games_log_dir.resolve())
    return payload


def _train_format(
    config_path: Path,
    *,
    edopro_home: Path,
    gateway_script: Path,
    games: int | None,
    max_decisions: int | None,
    timeout_seconds: float,
    output: Path,
    agent_a_policy: str = "heuristic",
    agent_b_policy: str = "heuristic",
    agent_a_weights: Path | None = None,
    agent_b_weights: Path | None = None,
) -> int:
    config = load_format_training_config(config_path)
    run_games = games if games is not None else config.games
    run_max_decisions = max_decisions if max_decisions is not None else config.max_decisions
    gateway_command = _gateway_command_string(
        [
            "node",
            str(gateway_script),
            "--edopro-home",
            str(edopro_home),
            "--max-decisions",
            str(run_max_decisions),
            "--deck-a",
            _deck_arg(config.deck_a),
            "--deck-b",
            _deck_arg(config.deck_b),
        ]
    )
    return _edopro_train(
        gateway_command,
        games=run_games,
        first_agent="bot-a",
        second_agent="bot-b",
        timeout_seconds=timeout_seconds,
        output=output,
        format_name=config.name,
        agent_a_policy=agent_a_policy,
        agent_b_policy=agent_b_policy,
    )


def _load_custom_deck_file(path: Path) -> FormatDeck:
    from ygotrainingbot.deck_composition import normalize_deck_dict

    payload = json.loads(path.read_text(encoding="utf-8"))
    normalized = normalize_deck_dict(payload, modern=True)
    deck = FormatDeck(
        name=str(normalized["name"]),
        main=tuple(normalized["main"]),
        extra=tuple(normalized.get("extra", [])),
        side=tuple(normalized.get("side", [])),
        archetype=str(normalized.get("archetype", "custom")),
        source="custom-ydk",
    )
    deck.validate()
    return deck


def _train_format_pack(
    pack_path: Path,
    *,
    edopro_home: Path,
    gateway_script: Path,
    games_per_matchup: int | None,
    max_decisions: int | None,
    timeout_seconds: float,
    output: Path,
    agent_a_policy: str = "heuristic",
    agent_b_policy: str = "heuristic",
    agent_a_weights: Path | None = None,
    agent_b_weights: Path | None = None,
    deck_a_name: str | None = None,
    deck_b_name: str | None = None,
    custom_deck_a_file: Path | None = None,
    custom_deck_b_file: Path | None = None,
) -> int:
    from ygotrainingbot.format_matrix import duel_mode_for_pack

    pack = load_format_pack(pack_path)
    run_games = games_per_matchup if games_per_matchup is not None else pack.games
    run_max_decisions = max_decisions if max_decisions is not None else pack.max_decisions
    duel_mode = duel_mode_for_pack(pack)
    matchups: list[dict[str, object]] = []
    total_games = 0
    total_decisions = 0
    aggregate_tags: dict[str, int] = {}

    games_log_dir = output.parent / "games" if output else None
    custom_deck_a = _load_custom_deck_file(custom_deck_a_file) if custom_deck_a_file else None
    custom_deck_b = _load_custom_deck_file(custom_deck_b_file) if custom_deck_b_file else None
    if custom_deck_a and custom_deck_b:
        deck_pairs = [(custom_deck_a, custom_deck_b)]
    elif custom_deck_a and deck_b_name:
        deck_pairs = [
            (custom_deck_a, second_deck)
            for second_deck in pack.decks
            if second_deck.name == deck_b_name
        ]
    elif custom_deck_a:
        deck_pairs = [
            (custom_deck_a, second_deck)
            for second_deck in pack.decks
            if second_deck.name != custom_deck_a.name
        ]
    elif custom_deck_b and deck_a_name:
        deck_pairs = [
            (first_deck, custom_deck_b)
            for first_deck in pack.decks
            if first_deck.name == deck_a_name
        ]
    elif custom_deck_b:
        deck_pairs = [
            (first_deck, custom_deck_b)
            for first_deck in pack.decks
            if first_deck.name != custom_deck_b.name
        ]
    else:
        deck_pairs = [
            (first_deck, second_deck)
            for first_deck in pack.decks
            if not deck_a_name or first_deck.name == deck_a_name
            for second_deck in pack.decks
            if (not deck_b_name or second_deck.name == deck_b_name)
            and not (deck_a_name and not deck_b_name and first_deck.name == second_deck.name)
        ]

    for first_deck, second_deck in deck_pairs:
        report = _collect_edopro_training_report(
            _gateway_command_for_decks(
                gateway_script,
                edopro_home=edopro_home,
                max_decisions=run_max_decisions,
                first_deck=first_deck,
                second_deck=second_deck,
                duel_mode=duel_mode,
            ),
            games=run_games,
            first_agent="bot-a",
            second_agent="bot-b",
            timeout_seconds=timeout_seconds,
            format_name=pack.name,
            agent_a_policy=agent_a_policy,
            agent_b_policy=agent_b_policy,
            agent_a_weights=agent_a_weights,
            agent_b_weights=agent_b_weights,
            games_log_dir=games_log_dir,
            matchup_label=f"{first_deck.name}_vs_{second_deck.name}",
            deck_a=first_deck,
            deck_b=second_deck,
        )
        total_games += int(report["games"])
        total_decisions += int(report["traced_decisions"])
        for tag, count in dict(report["tags"]).items():
            aggregate_tags[str(tag)] = aggregate_tags.get(str(tag), 0) + int(count)
        matchups.append(
            {
                "deck_a": first_deck.name,
                "deck_b": second_deck.name,
                "report": report,
            }
        )

    pack_report = {
        "format": pack.name,
        "description": pack.description,
        "banlist": {
            "forbidden": list(pack.banlist.forbidden),
            "limited": list(pack.banlist.limited),
            "semi_limited": list(pack.banlist.semi_limited),
        },
        "decks": [
            {
                "name": deck.name,
                "archetype": deck.archetype,
                "source": deck.source,
                "main_count": len(deck.main),
            }
            for deck in pack.decks
        ],
        "games_per_matchup": run_games,
        "agent_a_policy": agent_a_policy,
        "agent_b_policy": agent_b_policy,
        "agent_a_weights": str(agent_a_weights) if agent_a_weights else None,
        "agent_b_weights": str(agent_b_weights) if agent_b_weights else None,
        "max_decisions": run_max_decisions,
        "total_games": total_games,
        "total_traced_decisions": total_decisions,
        "aggregate_tags": aggregate_tags,
        "matchups": matchups,
    }
    report_json = json.dumps(pack_report, indent=2, sort_keys=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report_json + "\n", encoding="utf-8")
    print(report_json)
    return 0


def _compare_agents(
    pack_path: Path,
    *,
    edopro_home: Path,
    gateway_script: Path,
    candidate_policy: str,
    baseline_policy: str,
    candidate_weights: Path | None,
    baseline_weights: Path | None,
    games_per_matchup: int,
    max_decisions: int | None,
    timeout_seconds: float,
    output: Path,
) -> int:
    report = _compare_agents_report(
        pack_path,
        edopro_home=edopro_home,
        gateway_script=gateway_script,
        candidate_policy=candidate_policy,
        baseline_policy=baseline_policy,
        candidate_weights=candidate_weights,
        baseline_weights=baseline_weights,
        games_per_matchup=games_per_matchup,
        max_decisions=max_decisions,
        timeout_seconds=timeout_seconds,
    )
    _write_report(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _benchmark_agents(
    pack_path: Path,
    *,
    edopro_home: Path,
    gateway_script: Path,
    policies: tuple[str, ...],
    baseline_policy: str,
    games_per_matchup: int,
    max_decisions: int | None,
    timeout_seconds: float,
    output: Path,
) -> int:
    comparisons = [
        _compare_agents_report(
            pack_path,
            edopro_home=edopro_home,
            gateway_script=gateway_script,
            candidate_policy=policy,
            baseline_policy=baseline_policy,
            games_per_matchup=games_per_matchup,
            max_decisions=max_decisions,
            timeout_seconds=timeout_seconds,
        )
        for policy in policies
    ]
    ranked = sorted(
        comparisons,
        key=lambda report: (
            float(report["candidate_decisive_win_rate"]),
            float(report["candidate_win_rate"]),
            -int(report["draws"]),
        ),
        reverse=True,
    )
    benchmark = {
        "pack": str(pack_path),
        "baseline_policy": baseline_policy,
        "candidate_weights": str(candidate_weights) if candidate_weights else None,
        "baseline_weights": str(baseline_weights) if baseline_weights else None,
        "games_per_matchup": games_per_matchup,
        "ranked_policies": [
            {
                "policy": report["candidate_policy"],
                "candidate_win_rate": report["candidate_win_rate"],
                "candidate_decisive_win_rate": report["candidate_decisive_win_rate"],
                "candidate_wins": report["candidate_wins"],
                "baseline_wins": report["baseline_wins"],
                "draws": report["draws"],
                "total_games": report["total_games"],
            }
            for report in ranked
        ],
        "comparisons": comparisons,
    }
    _write_report(output, benchmark)
    print(json.dumps(benchmark, indent=2, sort_keys=True))
    return 0


def _compare_agents_report(
    pack_path: Path,
    *,
    edopro_home: Path,
    gateway_script: Path,
    candidate_policy: str,
    baseline_policy: str,
    candidate_weights: Path | None = None,
    baseline_weights: Path | None = None,
    games_per_matchup: int = 5,
    max_decisions: int | None,
    timeout_seconds: float,
) -> dict[str, object]:
    pack = load_format_pack(pack_path)
    run_max_decisions = max_decisions if max_decisions is not None else pack.max_decisions
    candidate_wins = 0
    baseline_wins = 0
    draws = 0
    total_games = 0
    total_decisions = 0
    matchups: list[dict[str, object]] = []

    for first_deck in pack.decks:
        for second_deck in pack.decks:
            command = _gateway_command_for_decks(
                gateway_script,
                edopro_home=edopro_home,
                max_decisions=run_max_decisions,
                first_deck=first_deck,
                second_deck=second_deck,
            )
            candidate_first = _collect_edopro_training_report(
                command,
                games=games_per_matchup,
                first_agent="candidate",
                second_agent="baseline",
                timeout_seconds=timeout_seconds,
                format_name=pack.name,
                agent_a_policy=candidate_policy,
                agent_b_policy=baseline_policy,
                agent_a_weights=candidate_weights,
                agent_b_weights=baseline_weights,
            )
            baseline_first = _collect_edopro_training_report(
                command,
                games=games_per_matchup,
                first_agent="baseline",
                second_agent="candidate",
                timeout_seconds=timeout_seconds,
                format_name=pack.name,
                agent_a_policy=baseline_policy,
                agent_b_policy=candidate_policy,
                agent_a_weights=baseline_weights,
                agent_b_weights=candidate_weights,
            )

            for report in (candidate_first, baseline_first):
                wins = dict(report["wins_by_agent"])
                candidate_wins += int(wins.get("candidate", 0))
                baseline_wins += int(wins.get("baseline", 0))
                draws += int(report["draws"])
                total_games += int(report["games"])
                total_decisions += int(report["traced_decisions"])

            matchups.append(
                {
                    "deck_a": first_deck.name,
                    "deck_b": second_deck.name,
                    "candidate_first": candidate_first,
                    "baseline_first": baseline_first,
                }
            )

    decisive_games = candidate_wins + baseline_wins
    return {
        "format": pack.name,
        "candidate_policy": candidate_policy,
        "baseline_policy": baseline_policy,
        "candidate_weights": str(candidate_weights) if candidate_weights else None,
        "baseline_weights": str(baseline_weights) if baseline_weights else None,
        "games_per_matchup": games_per_matchup,
        "max_decisions": run_max_decisions,
        "total_games": total_games,
        "total_traced_decisions": total_decisions,
        "candidate_wins": candidate_wins,
        "baseline_wins": baseline_wins,
        "draws": draws,
        "candidate_win_rate": candidate_wins / total_games if total_games else 0.0,
        "baseline_win_rate": baseline_wins / total_games if total_games else 0.0,
        "candidate_decisive_win_rate": candidate_wins / decisive_games if decisive_games else 0.0,
        "matchups": matchups,
    }


def _write_report(output: Path, report: dict[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def _load_policy_weights(policy_path: Path | None) -> dict[str, float] | None:
    from ygotrainingbot.policy_runtime import scaled_tag_weights_for_play

    if policy_path is None:
        return None
    if not policy_path.exists():
        raise ValueError(f"learned policy does not exist: {policy_path}")
    return scaled_tag_weights_for_play(policy_path)


def _gateway_command_base(
    gateway_script: Path,
    *,
    edopro_home: Path,
    max_decisions: int,
    duel_mode: str = "mr3",
) -> str:
    return _gateway_command_string(
        [
            "node",
            str(gateway_script),
            "--edopro-home",
            str(edopro_home),
            "--max-decisions",
            str(max_decisions),
            "--duel-mode",
            duel_mode,
        ]
    )


def _gateway_command_for_decks(
    gateway_script: Path,
    *,
    edopro_home: Path,
    max_decisions: int,
    first_deck: FormatDeck,
    second_deck: FormatDeck,
    duel_mode: str = "mr3",
) -> str:
    return _gateway_command_string(
        [
            "node",
            str(gateway_script),
            "--edopro-home",
            str(edopro_home),
            "--max-decisions",
            str(max_decisions),
            "--duel-mode",
            duel_mode,
            "--deck-a",
            _deck_arg(first_deck.main),
            "--deck-b",
            _deck_arg(second_deck.main),
        ]
    )


def _gateway_command_string(parts: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


def _deck_arg(cards: tuple[int, ...]) -> str:
    return ",".join(str(card_id) for card_id in cards)


def _print_human_report(report: StaticTrainingReport) -> None:
    print(f"Analyzed {report.cards_analyzed} cards across {report.sets_analyzed} sets.")
    if report.top_effect_tags:
        tags = ", ".join(f"{tag}={count}" for tag, count in report.top_effect_tags)
        print(f"Top effect signals: {tags}")

    print("\nSet profiles:")
    for profile in report.set_profiles[:10]:
        tags = ", ".join(f"{tag}={count}" for tag, count in profile.top_effect_tags)
        print(f"- {profile.set_code} ({profile.card_count} cards): {tags or 'no tags'}")

    print("\nInteraction candidates:")
    for candidate in report.interaction_candidates[:20]:
        card_pair = " + ".join(candidate.cards)
        signals = ", ".join(candidate.shared_signals)
        print(f"- {candidate.set_code}: {card_pair} [{signals}]")


def _report_to_dict(report: StaticTrainingReport) -> dict[str, object]:
    return {
        "sets_analyzed": report.sets_analyzed,
        "cards_analyzed": report.cards_analyzed,
        "top_effect_tags": list(report.top_effect_tags),
        "set_profiles": [
            {
                "set_code": profile.set_code,
                "set_name": profile.set_name,
                "card_count": profile.card_count,
                "top_archetypes": list(profile.top_archetypes),
                "top_effect_tags": list(profile.top_effect_tags),
            }
            for profile in report.set_profiles
        ],
        "interaction_candidates": [
            {
                "set_code": candidate.set_code,
                "set_name": candidate.set_name,
                "cards": list(candidate.cards),
                "shared_signals": list(candidate.shared_signals),
                "reason": candidate.reason,
            }
            for candidate in report.interaction_candidates
        ],
    }


def _index_training_db(roots: Sequence[Path], *, db_path: Path) -> int:
    from ygotrainingbot.training_db import connect, database_summary, index_roots

    conn = connect(db_path)
    stats = index_roots(conn, roots)
    summary = database_summary(conn)
    payload = {**stats, **summary, "db": str(db_path.resolve())}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _query_training_db(
    *,
    db_path: Path,
    bot_id: str,
    opponent: str | None,
    going_first: bool,
    going_second: bool,
    limit: int,
) -> int:
    from ygotrainingbot.training_db import bot_game_record, connect, query_games

    conn = connect(db_path)
    goes_first = True if going_first else False if going_second else None
    record = bot_game_record(conn, bot_id, goes_first=goes_first, opponent_bot=opponent)
    games = query_games(conn, bot_id=bot_id, opponent_bot=opponent, limit=limit)
    print(json.dumps({"record": record, "recent_games": games}, indent=2, sort_keys=True))
    return 0


def _deck_analytics(*, db_path: Path, bot_id: str, apply_to_policy: Path | None) -> int:
    from ygotrainingbot.duel_analytics import (
        analytics_to_learning_nudges,
        card_tag_contributions,
        deck_analytics,
        opponent_breakdown,
    )
    from ygotrainingbot.policy_runtime import read_policy_file, write_policy_file
    from ygotrainingbot.training_db import connect

    conn = connect(db_path)
    analytics = deck_analytics(conn, bot_id)
    payload = {
        "bot_id": bot_id,
        "analytics": analytics,
        "opponents": opponent_breakdown(conn, bot_id),
        "tag_contributions": card_tag_contributions(conn, bot_id),
        "learning_nudges": analytics_to_learning_nudges(analytics),
    }
    if apply_to_policy is not None:
        previous = read_policy_file(apply_to_policy)
        merged = dict(previous.get("tag_weights", {}))
        for tag, delta in payload["learning_nudges"].items():
            merged[str(tag)] = float(merged.get(str(tag), 0.0)) + float(delta)
        write_policy_file(apply_to_policy, merged, observations=int(previous.get("observations", 0)))
        payload["applied_policy"] = str(apply_to_policy.resolve())
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _run_experiment(
    *,
    pack: Path,
    deck_a_index: int,
    deck_b_index: int,
    games: int,
    policy_a: str,
    policy_b: str,
    weights_a: Path | None,
    weights_b: Path | None,
    edopro_home: Path,
    gateway_script: Path,
    max_decisions: int,
    timeout_seconds: float,
    output: Path,
) -> int:
    from ygotrainingbot.experiments import run_head_to_head_experiment
    from ygotrainingbot.format_training import load_format_pack

    format_pack = load_format_pack(pack)
    if deck_a_index >= len(format_pack.decks) or deck_b_index >= len(format_pack.decks):
        raise ValueError(f"deck index out of range for pack with {len(format_pack.decks)} decks.")
    deck_a = format_pack.decks[deck_a_index]
    deck_b = format_pack.decks[deck_b_index]
    gateway = _gateway_command_base(
        gateway_script,
        edopro_home=edopro_home,
        max_decisions=max_decisions,
    )

    def play_duel_wrapper(gateway_command: str, **kwargs) -> dict[str, object]:
        return _play_single_duel_report(
            gateway_command,
            first_agent=kwargs["first_agent"],
            second_agent=kwargs["second_agent"],
            agent_a_policy=kwargs["first_policy"],
            agent_b_policy=kwargs["second_policy"],
            agent_a_weights=kwargs["first_weights"],
            agent_b_weights=kwargs["second_weights"],
            deck_a=kwargs["first_deck"],
            deck_b=kwargs["second_deck"],
            seed=kwargs["seed"],
            timeout_seconds=kwargs["timeout_seconds"],
            format_name=kwargs.get("format_name"),
            require_clean_scripts=True,
        )

    result = run_head_to_head_experiment(
        play_duel=play_duel_wrapper,
        gateway_command=gateway,
        deck_a=deck_a,
        deck_b=deck_b,
        agent_a="deck-a",
        agent_b="deck-b",
        policy_a=policy_a,
        policy_b=policy_b,
        weights_a=weights_a,
        weights_b=weights_b,
        games=games,
        timeout_seconds=timeout_seconds,
        format_name=format_pack.name,
    )
    payload = {
        "pack": str(pack.resolve()),
        "deck_a": deck_a.name,
        "deck_b": deck_b.name,
        "policy_a": policy_a,
        "policy_b": policy_b,
        "result": result.to_dict(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _run_chronological_curriculum(
    *,
    packs: Sequence[Path] | None,
    edopro_home: Path,
    gateway_script: Path,
    policy: str,
    current_policy: Path,
    games_per_matchup: int,
    promotion_games_per_matchup: int | None,
    max_decisions: int,
    timeout_seconds: float,
    promote_to: Path,
    output_dir: Path,
    roster_path: Path,
    bracket_years: list[int] | None,
    series_per_opponent: int,
    ethan_bot_id: str,
) -> int:
    from ygotrainingbot.chronological import build_chronological_plan, chronological_pack_paths, write_chronological_plan

    resolved_packs = list(packs) if packs else chronological_pack_paths(Path.cwd())
    plan = build_chronological_plan(
        repo_root=Path.cwd(),
        packs=resolved_packs,
        roster_path=roster_path,
        bracket_years=bracket_years or [2010],
    )
    write_chronological_plan(output_dir / "chronological-plan.json", plan)

    status = _train_format_curriculum(
        resolved_packs,
        edopro_home=edopro_home,
        gateway_script=gateway_script,
        policy=policy,
        current_policy=current_policy,
        games_per_matchup=games_per_matchup,
        promotion_games_per_matchup=promotion_games_per_matchup,
        max_decisions=max_decisions,
        timeout_seconds=timeout_seconds,
        promote_to=promote_to,
        output_dir=output_dir / "format-stages",
    )
    if status != 0:
        return status

    bracket_dir = output_dir / "bracket"
    years = bracket_years or [2010]
    for year in years:
        bracket_status = _run_yearly_bracket(
            roster_path=roster_path,
            edopro_home=edopro_home,
            gateway_script=gateway_script,
            start_year=year,
            end_year=year,
            series_per_opponent=series_per_opponent,
            max_decisions=max_decisions,
            timeout_seconds=timeout_seconds,
            ethan_bot_id=ethan_bot_id,
            default_runtime_profile="balanced",
            ethan_runtime_profile="balanced",
            master_seed=year,
            output_dir=bracket_dir,
            allow_script_errors=False,
        )
        if bracket_status != 0:
            return bracket_status
        _index_training_db([bracket_dir], db_path=output_dir / "training.db")
    print(json.dumps({"plan": plan, "promoted_policy": str(promote_to.resolve())}, indent=2, sort_keys=True))
    return 0


def _ask_training(
    *,
    db_path: Path,
    question: str,
    bot_id: str,
    progress_dir: Path | None,
) -> int:
    from ygotrainingbot.research_assistant import answer_training_question

    answer = answer_training_question(
        db_path,
        question,
        default_bot_id=bot_id,
        progress_dir=progress_dir,
    )
    print(answer)
    return 0


def _test_format_matrix(
    *,
    packs: list[Path],
    edopro_home: Path,
    gateway_script: Path,
    games_per_matchup: int,
    max_decisions: int,
    timeout_seconds: float,
    policy: str,
    output: Path,
) -> int:
    from ygotrainingbot.format_matrix import run_format_matrix

    def play_duel_wrapper(gateway_command: str, **kwargs) -> dict[str, object]:
        return _play_single_duel_report(
            gateway_command,
            first_agent=kwargs["first_agent"],
            second_agent=kwargs["second_agent"],
            agent_a_policy=kwargs["first_policy"],
            agent_b_policy=kwargs["second_policy"],
            agent_a_weights=kwargs["first_weights"],
            agent_b_weights=kwargs["second_weights"],
            deck_a=kwargs["first_deck"],
            deck_b=kwargs["second_deck"],
            seed=kwargs["seed"],
            timeout_seconds=kwargs["timeout_seconds"],
            format_name=kwargs.get("format_name"),
            require_clean_scripts=True,
        )

    def gateway_for_mode(duel_mode: str) -> str:
        return _gateway_command_base(
            gateway_script,
            edopro_home=edopro_home,
            max_decisions=max_decisions,
            duel_mode=duel_mode,
        )

    report = run_format_matrix(
        packs=packs,
        play_duel=play_duel_wrapper,
        gateway_command_for_mode=gateway_for_mode,
        games_per_matchup=games_per_matchup,
        policy=policy,
        timeout_seconds=timeout_seconds,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("passed") else 1


def _export_edopro_watch(
    *,
    bracket_dir: Path,
    output_dir: Path | None,
    year: int,
    count: int,
    seed: int | None,
    edopro_deck_dir: Path | None,
) -> int:
    from ygotrainingbot.edopro_watch import export_random_watch_bundles

    destination = output_dir or (bracket_dir / "edopro-watch")
    manifest = export_random_watch_bundles(
        bracket_dir,
        destination,
        year=year,
        count=count,
        edopro_deck_dir=edopro_deck_dir,
        seed=seed,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
