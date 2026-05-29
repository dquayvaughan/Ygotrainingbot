#!/usr/bin/env python3
"""Fetch tournament deck lists from YGOPRODeck and write configs/ygoprodeck-deck-cache.json."""

from __future__ import annotations

import argparse
from pathlib import Path

from ygotrainingbot.ygoprodeck_decks import build_deck_cache


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch YGOPRODeck tournament decks into a local cache.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("configs/ygoprodeck-deck-cache.json"),
        help="Path to write the deck cache JSON.",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=Path("configs/ygoprodeck-deck-sources.json"),
        help="Curated pretty_url map for hard-to-find archetypes.",
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    cache = build_deck_cache(
        repo_root=repo_root,
        output_path=repo_root / args.output,
        sources_path=repo_root / args.sources,
    )
    print(f"Cached {len(cache)} YGOPRODeck tournament decks to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
