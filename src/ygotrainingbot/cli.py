"""Command line tools for bootstrapping and running training passes."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Sequence

from ygotrainingbot.agents import FirstLegalActionAgent
from ygotrainingbot.data import (
    build_card_sets,
    fetch_ygoprodeck_cards,
    load_card_database,
    save_card_database,
)
from ygotrainingbot.edopro import EdoproGatewayConfig, EdoproInstall, JsonLineEdoproSimulator
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
    edopro_train_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    edopro_train_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON file for the gameplay training report.",
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
        )
    raise ValueError(f"Unknown command {args.command!r}.")


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
        FirstLegalActionAgent(first_agent),
        FirstLegalActionAgent(second_agent),
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
) -> int:
    if games < 1:
        raise ValueError("games must be at least 1.")

    wins_by_agent: dict[str, int] = {}
    tags: dict[str, int] = {}
    total_decisions = 0
    draws = 0

    for _ in range(games):
        config = EdoproGatewayConfig.from_shell_words(
            shlex.split(gateway_command),
            timeout_seconds=timeout_seconds,
        )
        result = JsonLineEdoproSimulator(config).play(
            FirstLegalActionAgent(first_agent),
            FirstLegalActionAgent(second_agent),
        )
        total_decisions += len(result.traces)
        if result.winner is None:
            draws += 1
        else:
            wins_by_agent[result.winner] = wins_by_agent.get(result.winner, 0) + 1
        for tag in result.tags:
            tags[tag] = tags.get(tag, 0) + 1
        for trace in result.traces:
            for tag in trace.action.tags:
                tags[tag] = tags.get(tag, 0) + 1

    report = {
        "games": games,
        "draws": draws,
        "traced_decisions": total_decisions,
        "wins_by_agent": wins_by_agent,
        "tags": tags,
    }
    report_json = json.dumps(report, indent=2, sort_keys=True)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report_json + "\n", encoding="utf-8")
    print(report_json)
    return 0


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
