"""Generate format-pack JSON files for every banlist period."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ygotrainingbot.banlist_catalog import TOP5_BY_PERIOD, BanlistPeriod, banlist_periods
from ygotrainingbot.deck_composition import normalize_deck_dict
from ygotrainingbot.meta_deck_templates import build_deck_shell

DEFAULT_GAMES = 25
DEFAULT_MAX_DECISIONS = 600


def _load_pack_banlist(repo_root: Path, stem: str) -> dict[str, list[int]]:
    path = repo_root / "configs" / "format-packs" / f"{stem}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    banlist = payload.get("banlist", {})
    return {
        "forbidden": list(banlist.get("forbidden", [])),
        "limited": list(banlist.get("limited", [])),
        "semi_limited": list(banlist.get("semi_limited", [])),
    }


def banlist_payload_for_period(period: BanlistPeriod, repo_root: Path) -> dict[str, list[int]]:
    if period.inherit_pack:
        return _load_pack_banlist(repo_root, period.inherit_pack)
    if period.year <= 2016:
        return _load_pack_banlist(repo_root, "edison-2010")
    return {"forbidden": [], "limited": [], "semi_limited": []}


def build_format_pack(period: BanlistPeriod, *, repo_root: Path) -> dict[str, Any]:
    modern = period.year >= 2017
    decks = [
        normalize_deck_dict(
            build_deck_shell(archetype, repo_root=repo_root, period_id=period.period_id, modern=modern),
            modern=modern,
            pad_zones=False,
        )
        for archetype in period.top5
    ]
    return {
        "name": period.pack_name,
        "banlist": banlist_payload_for_period(period, repo_root),
        "banlist_source": f"TCG {period.label} — representative limits for training metadata.",
        "banlist_period": period.period_id,
        "banlist_label": period.label,
        "description": (
            f"Top-5 meta deck shells for {period.label}. "
            "Banlist metadata is recorded for analysis; gateway enforcement is a future step."
        ),
        "games": DEFAULT_GAMES,
        "max_decisions": DEFAULT_MAX_DECISIONS,
        "decks": decks,
    }


def write_format_packs(repo_root: Path, *, output_dir: Path | None = None) -> list[Path]:
    out_dir = output_dir or (repo_root / "configs" / "format-packs" / "banlists")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for period in banlist_periods():
        payload = build_format_pack(period, repo_root=repo_root)
        path = out_dir / f"{period.pack_name}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


def expand_edison_pack(repo_root: Path) -> None:
    """Ensure edison-2010.json contains the March 2010 top-5 decks."""

    from ygotrainingbot.banlist_catalog import TOP5_BY_PERIOD

    path = repo_root / "configs" / "format-packs" / "edison-2010.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["decks"] = [
        normalize_deck_dict(
            build_deck_shell(archetype, repo_root=repo_root, period_id="2010-03", modern=False),
            modern=False,
            pad_zones=False,
        )
        for archetype in TOP5_BY_PERIOD["2010-03"]
    ]
    payload["banlist_period"] = "2010-03"
    payload["banlist_label"] = "March 2010 (Edison)"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def expand_goat_pack(repo_root: Path) -> None:
    """Ensure goat-2005.json contains five representative Goat-era shells."""

    path = repo_root / "configs" / "format-packs" / "goat-2005.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    top5 = ("Goat Control", "Chaos Warrior", "Goat Control", "Chaos Warrior", "Goat Control")
    # Unique top goat meta: Control, Chaos, Flip Flop Goat variant, Warrior, Turbo
    top5_unique = ("Goat Control", "Chaos Warrior", "Machina Gadget", "Gravekeeper", "X-Saber")
    payload["decks"] = [
        normalize_deck_dict(
            build_deck_shell(archetype, repo_root=repo_root, period_id="2005-04", modern=False),
            modern=False,
            pad_zones=False,
        )
        for archetype in top5_unique
    ]
    payload["banlist_period"] = "2005-04"
    payload["banlist_label"] = "April 2005 (Goat)"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
