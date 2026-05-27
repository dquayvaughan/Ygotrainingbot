"""Command line tools for bootstrapping and running training passes."""

from __future__ import annotations

import argparse
import json
import shlex
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
from ygotrainingbot.learning import learn_from_report
from ygotrainingbot.static_training import StaticSetTrainer, StaticTrainingReport


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
    train_pack_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    train_pack_parser.add_argument("--agent-a-policy", default="heuristic")
    train_pack_parser.add_argument("--agent-b-policy", default="heuristic")
    train_pack_parser.add_argument("--agent-a-weights", type=Path, default=None)
    train_pack_parser.add_argument("--agent-b-weights", type=Path, default=None)
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
    raise ValueError(f"Unknown command {args.command!r}.")


def _learn_from_report(report: Path, *, policy: Path, summary: Path) -> int:
    _analysis, english = learn_from_report(report, policy)
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(english, encoding="utf-8")
    print(english)
    return 0


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
        shlex.split(gateway_command),
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
) -> dict[str, object]:
    if games < 1:
        raise ValueError("games must be at least 1.")

    wins_by_agent: dict[str, int] = {}
    tags: dict[str, int] = {}
    loaded_agent_a_weights = _load_policy_weights(agent_a_weights)
    loaded_agent_b_weights = _load_policy_weights(agent_b_weights)
    action_counts: dict[str, int] = {}
    decision_samples: list[dict[str, object]] = []
    engine_log_samples: list[object] = []
    total_decisions = 0
    draws = 0

    for _ in range(games):
        config = EdoproGatewayConfig.from_shell_words(
            shlex.split(gateway_command),
            timeout_seconds=timeout_seconds,
        )
        result = JsonLineEdoproSimulator(config).play(
            create_agent(agent_a_policy, first_agent, loaded_agent_a_weights),
            create_agent(agent_b_policy, second_agent, loaded_agent_b_weights),
        )
        total_decisions += len(result.traces)
        for log_entry in result.metadata.get("gateway_logs", ()):
            if len(engine_log_samples) < 160:
                engine_log_samples.append(log_entry)
        for trace in result.traces:
            action_counts[trace.action.action_id] = action_counts.get(trace.action.action_id, 0) + 1
            if len(decision_samples) < 25:
                decision_samples.append(
                    {
                        "turn": trace.state.turn,
                        "agent": trace.agent_name,
                        "summary": trace.state.summary,
                        "selected_action": trace.action.action_id,
                        "selected_label": trace.action.label,
                        "selected_tags": list(trace.action.tags),
                        "selected_expected_value": trace.action.expected_value,
                        "public_zones": {
                            key: list(value) for key, value in trace.state.public_zones.items()
                        },
                        "evaluation": trace.note,
                    }
                )
        if result.winner is None:
            draws += 1
        else:
            wins_by_agent[result.winner] = wins_by_agent.get(result.winner, 0) + 1
        for tag in result.tags:
            tags[tag] = tags.get(tag, 0) + 1
        for trace in result.traces:
            for tag in trace.action.tags:
                tags[tag] = tags.get(tag, 0) + 1

    return {
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
        "decision_samples": decision_samples,
        "engine_log_samples": engine_log_samples,
    }


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
    gateway_command = shlex.join(
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
) -> int:
    pack = load_format_pack(pack_path)
    run_games = games_per_matchup if games_per_matchup is not None else pack.games
    run_max_decisions = max_decisions if max_decisions is not None else pack.max_decisions
    matchups: list[dict[str, object]] = []
    total_games = 0
    total_decisions = 0
    aggregate_tags: dict[str, int] = {}

    for first_deck in pack.decks:
        for second_deck in pack.decks:
            report = _collect_edopro_training_report(
                _gateway_command_for_decks(
                    gateway_script,
                    edopro_home=edopro_home,
                    max_decisions=run_max_decisions,
                    first_deck=first_deck,
                    second_deck=second_deck,
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
    if policy_path is None:
        return None
    if not policy_path.exists():
        raise ValueError(f"learned policy does not exist: {policy_path}")
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return {
        str(tag): float(weight)
        for tag, weight in dict(payload.get("tag_weights", {})).items()
    }


def _gateway_command_for_decks(
    gateway_script: Path,
    *,
    edopro_home: Path,
    max_decisions: int,
    first_deck: FormatDeck,
    second_deck: FormatDeck,
) -> str:
    return shlex.join(
        [
            "node",
            str(gateway_script),
            "--edopro-home",
            str(edopro_home),
            "--max-decisions",
            str(max_decisions),
            "--deck-a",
            _deck_arg(first_deck.main),
            "--deck-b",
            _deck_arg(second_deck.main),
        ]
    )


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


if __name__ == "__main__":
    raise SystemExit(main())
