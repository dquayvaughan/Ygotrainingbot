#!/usr/bin/env python3
"""Pick random bracket games and export EDOPro watch bundles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ygotrainingbot.edopro_watch import export_random_watch_bundles  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bracket-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--year", type=int, default=2010)
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--edopro-deck-dir",
        type=Path,
        default=Path("C:/ProjectIgnis/deck"),
        help="Also copy .ydk files here (set empty to skip).",
    )
    args = parser.parse_args()

    bracket_dir = args.bracket_dir
    output_dir = args.output_dir or (bracket_dir / "edopro-watch")
    edopro_dir = args.edopro_deck_dir if str(args.edopro_deck_dir) else None

    manifest = export_random_watch_bundles(
        bracket_dir,
        output_dir,
        year=args.year,
        count=args.count,
        edopro_deck_dir=edopro_dir,
        seed=args.seed,
    )
    print(json.dumps({"output_dir": str(output_dir.resolve()), "bundles": manifest}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
