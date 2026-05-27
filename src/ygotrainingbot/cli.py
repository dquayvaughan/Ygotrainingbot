"""Command line tools for bootstrapping and running training passes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ygotrainingbot.data import (
    build_card_sets,
    fetch_ygoprodeck_cards,
    load_card_database,
    save_card_database,
)
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
