#!/usr/bin/env python3
"""Refresh curated YGOPRODeck tournament lists into the deck cache."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ygotrainingbot.ygoprodeck_decks import (
    DEFAULT_CACHE_PATH,
    DEFAULT_SOURCES_PATH,
    REQUEST_DELAY_SECONDS,
    deck_to_dict,
    load_deck_cache,
    load_deck_sources,
    save_deck_cache,
    scrape_deck_page,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    sources = load_deck_sources(args.repo_root / DEFAULT_SOURCES_PATH)
    cache = load_deck_cache(args.repo_root / DEFAULT_CACHE_PATH)
    for archetype, slug in sorted(sources.items()):
        scraped = scrape_deck_page(slug)
        time.sleep(REQUEST_DELAY_SECONDS)
        if len(scraped.main) < 40:
            print(f"skip {archetype}: {scraped.name} ({len(scraped.main)} main)")
            continue
        modern = archetype not in {
            "Quickdraw Dandywarrior",
            "Frog Monarch",
            "Plant Synchro",
            "Machina Gadget",
            "X-Saber",
            "Gravekeeper",
        }
        cache[archetype] = deck_to_dict(
            scraped,
            archetype=archetype,
            modern=modern,
            pad_zones=False,
        )
        print(f"cached {archetype}: {scraped.name} ({len(scraped.main)} main)")

    save_deck_cache(cache, args.repo_root / DEFAULT_CACHE_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
