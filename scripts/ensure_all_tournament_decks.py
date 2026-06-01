#!/usr/bin/env python3
"""Fetch, validate, and report tournament-accurate decks for every TOP5 archetype."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ygotrainingbot.banlist_catalog import TOP5_BY_PERIOD
from ygotrainingbot.deck_quality import is_synthesized_shell
from ygotrainingbot.format_pack_generator import write_format_packs
from ygotrainingbot.ygoprodeck_decks import (
    DEFAULT_CACHE_PATH,
    build_deck_cache,
    load_deck_cache,
    periods_for_archetype,
    resolve_deck_for_archetype,
    save_deck_cache,
    trusted_cache_entry,
)


def unique_archetypes() -> tuple[str, ...]:
    return tuple(sorted({name for top5 in TOP5_BY_PERIOD.values() for name in top5}))


def audit_cache(cache: dict[str, dict], *, repo_root: Path) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    placeholders: list[str] = []
    for archetype in unique_archetypes():
        entry = cache.get(archetype)
        if entry is None:
            missing.append(archetype)
            continue
        if is_synthesized_shell(entry):
            placeholders.append(archetype)
            continue
        if not trusted_cache_entry(archetype, entry, cache=cache, repo_root=repo_root):
            placeholders.append(archetype)
    return missing, placeholders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    cache_path = repo_root / DEFAULT_CACHE_PATH

    if not args.skip_fetch:
        print("Fetching tournament decks from YGOPRODeck (this may take several minutes)...")
        cache = build_deck_cache(repo_root=repo_root, output_path=DEFAULT_CACHE_PATH)
    else:
        cache = load_deck_cache(cache_path)

    missing, bad = audit_cache(cache, repo_root=repo_root)
    if missing:
        print(f"\nRetrying {len(missing)} missing archetypes individually...")
        for archetype in missing:
            period_list = periods_for_archetype(archetype)
            resolved = None
            for period in sorted(period_list, key=lambda item: item.sort_key, reverse=True):
                resolved = resolve_deck_for_archetype(
                    archetype,
                    period=period,
                    cache=cache,
                    modern=period.year >= 2017,
                    pad_zones=False,
                    repo_root=repo_root,
                )
                if resolved is not None:
                    break
            if resolved is None:
                resolved = resolve_deck_for_archetype(
                    archetype,
                    period=None,
                    cache=cache,
                    modern=True,
                    pad_zones=False,
                    repo_root=repo_root,
                )
            if resolved is not None:
                cache[archetype] = resolved
                print(f"  resolved {archetype}: {resolved.get('name')}")
            else:
                print(f"  still missing {archetype}")
        save_deck_cache(cache, cache_path)

    print("\nRegenerating banlist format packs...")
    write_format_packs(repo_root=repo_root)

    missing, bad = audit_cache(cache, repo_root=repo_root)
    pack_dir = repo_root / "configs/format-packs/banlists"
    shell_decks: list[str] = []
    for pack_path in sorted(pack_dir.glob("banlist-*.json")):
        payload = json.loads(pack_path.read_text(encoding="utf-8"))
        for deck in payload.get("decks", []):
            if is_synthesized_shell(deck):
                shell_decks.append(f"{pack_path.name}: {deck.get('name')}")

    print(f"\nCache: {len(cache)}/{len(unique_archetypes())} archetypes")
    if missing:
        print("Missing from cache:")
        for name in missing:
            print(f"  - {name}")
    if bad:
        print("Untrusted cache entries:")
        for name in bad:
            print(f"  - {name}")
    if shell_decks:
        print(f"Placeholder shells remaining in packs ({len(shell_decks)}):")
        for line in shell_decks[:20]:
            print(f"  - {line}")
        if len(shell_decks) > 20:
            print(f"  ... and {len(shell_decks) - 20} more")
    else:
        print("All format-pack decks are real tournament lists.")

    return 1 if missing or shell_decks else 0


if __name__ == "__main__":
    raise SystemExit(main())
