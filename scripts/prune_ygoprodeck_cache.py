#!/usr/bin/env python3
"""Remove polluted or invalid entries from the YGOPRODeck deck cache."""

from __future__ import annotations

import argparse
from pathlib import Path

from ygotrainingbot.ygoprodeck_decks import (
    DEFAULT_CACHE_PATH,
    load_deck_cache,
    save_deck_cache,
    search_keywords,
    signature_card_ids,
    trusted_cache_entry,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    args = parser.parse_args()

    cache_path = args.repo_root / args.cache
    cache = load_deck_cache(cache_path)
    kept: dict[str, dict] = {}
    removed: list[str] = []
    for archetype, entry in sorted(cache.items()):
        if trusted_cache_entry(archetype, entry, cache=cache, repo_root=args.repo_root):
            kept[archetype] = entry
        else:
            removed.append(archetype)

    save_deck_cache(kept, cache_path)
    print(f"Kept {len(kept)} cache entries, removed {len(removed)}")
    for name in removed:
        keywords = ", ".join(search_keywords(name)[:3])
        print(f"  - {name} ({keywords or 'no keywords'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
