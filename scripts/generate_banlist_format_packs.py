#!/usr/bin/env python3
"""Write banlist format packs under configs/format-packs/banlists/."""

from __future__ import annotations

import argparse
from pathlib import Path

from ygotrainingbot.format_pack_generator import expand_edison_pack, expand_goat_pack, write_format_packs


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate banlist era format packs.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    expand_edison_pack(repo_root)
    expand_goat_pack(repo_root)
    paths = write_format_packs(repo_root)
    print(f"Wrote {len(paths)} banlist format packs to configs/format-packs/banlists/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
