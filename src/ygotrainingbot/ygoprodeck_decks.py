"""Fetch tournament deck lists from YGOPRODeck (API + page scrape)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlencode

from ygotrainingbot.banlist_catalog import BanlistPeriod, banlist_periods
from ygotrainingbot.deck_composition import normalize_deck_dict

YGOPRODECK_GET_DECKS_URL = "https://ygoprodeck.com/api/decks/getDecks.php"
YGOPRODECK_DECK_PAGE = "https://ygoprodeck.com/deck/{pretty_url}"
USER_AGENT = "ygotrainingbot/1.0 (+https://github.com/Ygotrainingbot)"
REQUEST_DELAY_SECONDS = 0.35
MAX_REQUEST_RETRIES = 4
DEFAULT_CACHE_PATH = Path("configs/ygoprodeck-deck-cache.json")
DEFAULT_SOURCES_PATH = Path("configs/ygoprodeck-deck-sources.json")
DEFAULT_KEY_CARDS_PATH = Path("configs/archetype-key-cards.json")
CARDINFO_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"

_KEY_CARD_CACHE: dict[str, tuple[int, ...]] = {}


@dataclass(frozen=True, slots=True)
class YgoProDeckDeck:
    name: str
    archetype: str
    main: tuple[int, ...]
    extra: tuple[int, ...]
    side: tuple[int, ...]
    source: str
    pretty_url: str = ""
    deck_num: int | None = None
    tournament: str = ""


def _request_json(url: str, *, timeout: float = 30.0) -> Any:
    last_error: Exception | None = None
    for attempt in range(MAX_REQUEST_RETRIES):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt + 1 < MAX_REQUEST_RETRIES:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("request failed without response")


def _request_text(url: str, *, timeout: float = 30.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def load_key_cards(path: Path = DEFAULT_KEY_CARDS_PATH) -> dict[str, tuple[int, ...]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    result: dict[str, tuple[int, ...]] = {}
    for name, card_ids in payload.items():
        if name == "description":
            continue
        if isinstance(card_ids, list):
            result[str(name)] = tuple(int(card_id) for card_id in card_ids)
    return result


def _lookup_cards_by_archetype_token(token: str) -> tuple[int, ...]:
    url = f"{CARDINFO_URL}?{urlencode({'archetype': token, 'num': 12})}"
    try:
        payload = _request_json(url)
    except urllib.error.HTTPError:
        return ()
    cards = payload.get("data")
    if not isinstance(cards, list):
        return ()
    return tuple(int(card["id"]) for card in cards if isinstance(card, dict) and card.get("id"))


def signature_card_ids(archetype: str, *, repo_root: Path | None = None) -> tuple[int, ...]:
    if archetype in _KEY_CARD_CACHE:
        return _KEY_CARD_CACHE[archetype]

    key_path = (repo_root or Path.cwd()) / DEFAULT_KEY_CARDS_PATH
    key_cards = load_key_cards(key_path)
    if archetype in key_cards and key_cards[archetype]:
        _KEY_CARD_CACHE[archetype] = key_cards[archetype]
        return key_cards[archetype]

    from ygotrainingbot.meta_deck_templates import ARCHETYPE_SIGNATURES

    template = ARCHETYPE_SIGNATURES.get(archetype, {})
    ids: list[int] = []
    for card_id, copies in template.get("signatures", ()):
        if int(copies) > 0:
            ids.append(int(card_id))
    for card_id in template.get("extra", ()):
        ids.append(int(card_id))
    if ids:
        deduped = tuple(dict.fromkeys(ids))
        _KEY_CARD_CACHE[archetype] = deduped
        return deduped

    for token in search_keywords(archetype):
        if len(token) < 4:
            continue
        looked_up = _lookup_cards_by_archetype_token(token.title() if token.islower() else token)
        if looked_up:
            _KEY_CARD_CACHE[archetype] = looked_up
            return looked_up
        time.sleep(REQUEST_DELAY_SECONDS)

    _KEY_CARD_CACHE[archetype] = ()
    return ()


def search_keywords(archetype: str) -> tuple[str, ...]:
    tokens = re.split(r"[\s\-/]+", archetype.lower())
    return tuple(token for token in tokens if len(token) > 2)


def period_date_range(period: BanlistPeriod) -> tuple[str, str]:
    periods = banlist_periods()
    index = next(i for i, item in enumerate(periods) if item.period_id == period.period_id)
    start = f"{period.year}-{period.month:02d}-01"
    if index + 1 < len(periods):
        nxt = periods[index + 1]
        end_year = nxt.year
        end_month = nxt.month - 1
        if end_month < 1:
            end_month = 12
            end_year -= 1
        end = f"{end_year}-{end_month:02d}-28"
    else:
        end = f"{period.year + 1}-12-31"
    return start, end


def _parse_zone_json(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(card_id) for card_id in json.loads(raw)]


def deck_from_api_payload(raw: dict[str, Any], *, archetype: str) -> YgoProDeckDeck:
    tournament = raw.get("tournamentName") or ""
    placement = raw.get("tournamentPlacement") or ""
    tournament_label = tournament
    if tournament and placement:
        tournament_label = f"{tournament} ({placement})"
    source_bits = ["YGOPRODeck"]
    if raw.get("format"):
        source_bits.append(str(raw["format"]))
    if tournament_label:
        source_bits.append(tournament_label)
    if raw.get("username"):
        source_bits.append(f"pilot: {raw['username']}")
    return YgoProDeckDeck(
        name=str(raw.get("deck_name") or f"{archetype} tournament list"),
        archetype=archetype,
        main=tuple(_parse_zone_json(raw.get("main_deck"))),
        extra=tuple(_parse_zone_json(raw.get("extra_deck"))),
        side=tuple(_parse_zone_json(raw.get("side_deck"))),
        source=" — ".join(source_bits),
        pretty_url=str(raw.get("pretty_url") or ""),
        deck_num=int(raw["deckNum"]) if raw.get("deckNum") is not None else None,
        tournament=tournament_label,
    )


def scrape_deck_page(pretty_url: str) -> YgoProDeckDeck:
    slug = pretty_url.rsplit("/", 1)[-1] if pretty_url.startswith("http") else pretty_url
    html = _request_text(YGOPRODECK_DECK_PAGE.format(pretty_url=slug))
    title_match = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.IGNORECASE)
    deck_name = title_match.group(1).strip() if title_match else slug.replace("-", " ").title()

    zones: dict[str, list[int]] = {"main": [], "extra": [], "side": []}
    current = "main"
    for line in html.splitlines():
        lower = line.lower()
        if 'id="main_deck"' in lower:
            current = "main"
        elif 'id="extra_deck"' in lower:
            current = "extra"
        elif 'id="side_deck"' in lower:
            current = "side"
        for match in re.finditer(r'data-card="(\d+)"', line):
            zones[current].append(int(match.group(1)))

    return YgoProDeckDeck(
        name=deck_name,
        archetype="",
        main=tuple(zones["main"]),
        extra=tuple(zones["extra"]),
        side=tuple(zones["side"]),
        source=f"YGOPRODeck deck page ({slug})",
        pretty_url=slug,
    )


def _fetch_deck_search(**params: str | int) -> list[dict[str, Any]]:
    url = f"{YGOPRODECK_GET_DECKS_URL}?{urlencode(params)}"
    try:
        payload = _request_json(url)
    except urllib.error.HTTPError:
        return []
    if isinstance(payload, dict):
        if payload.get("error"):
            return []
        return [payload]
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and "main_deck" in item]


def _score_api_deck(
    raw: dict[str, Any],
    *,
    archetype: str,
    signature_ids: Sequence[int],
) -> int:
    main = _parse_zone_json(raw.get("main_deck"))
    extra = _parse_zone_json(raw.get("extra_deck"))
    all_cards = set(main + extra)
    name = str(raw.get("deck_name") or "").lower()
    keywords = search_keywords(archetype)
    matched = [keyword for keyword in keywords if keyword in name]
    score = len(matched) * 18
    if keywords and len(matched) == len(keywords):
        score += 30
    if len(keywords) >= 2 and len(matched) >= len(keywords) - 1:
        score += 12

    hits = sum(1 for card_id in signature_ids if card_id in all_cards)
    score += hits * 8

    if raw.get("tournamentName"):
        score += 12
    if raw.get("tournamentPlacement"):
        score += 6
    if raw.get("format") == "Tournament Meta Decks":
        score += 8
    if 40 <= len(main) <= 60:
        score += 4
    if len(extra) >= 10:
        score += 3
    if len(_parse_zone_json(raw.get("side_deck"))) >= 10:
        score += 3
    return score


def search_tournament_deck(
    archetype: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    deck_format: str = "Tournament Meta Decks",
    max_pages: int = 12,
    repo_root: Path | None = None,
) -> YgoProDeckDeck | None:
    signature_ids = signature_card_ids(archetype, repo_root=repo_root)
    best: tuple[int, dict[str, Any]] | None = None
    for page in range(max_pages):
        params: dict[str, str | int] = {
            "format": deck_format,
            "num": 50,
            "offset": page * 50,
        }
        if start_date and end_date:
            params["from"] = start_date
            params["to"] = end_date
        rows = _fetch_deck_search(**params)
        if not rows:
            break
        for raw in rows:
            score = _score_api_deck(raw, archetype=archetype, signature_ids=signature_ids)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, raw)
        time.sleep(REQUEST_DELAY_SECONDS)
    if best is None or best[0] < 20:
        return None
    deck = deck_from_api_payload(best[1], archetype=archetype)
    deck = YgoProDeckDeck(
        name=deck.name,
        archetype=archetype,
        main=deck.main,
        extra=deck.extra,
        side=deck.side,
        source=deck.source,
        pretty_url=deck.pretty_url,
        deck_num=deck.deck_num,
        tournament=deck.tournament,
    )
    return deck


def load_deck_sources(path: Path = DEFAULT_SOURCES_PATH) -> dict[str, str]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    decks = payload.get("decks", payload)
    if not isinstance(decks, dict):
        return {}
    return {str(name): str(slug) for name, slug in decks.items()}


def load_deck_cache(path: Path = DEFAULT_CACHE_PATH) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    decks = payload.get("decks", payload)
    if not isinstance(decks, dict):
        return {}
    return decks


def save_deck_cache(decks: dict[str, dict[str, Any]], path: Path = DEFAULT_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"decks": decks}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def deck_to_dict(deck: YgoProDeckDeck, *, archetype: str, modern: bool, pad_zones: bool) -> dict[str, Any]:
    payload = normalize_deck_dict(
        {
            "name": deck.name,
            "archetype": archetype,
            "source": deck.source,
            "main": list(deck.main),
            "extra": list(deck.extra),
            "side": list(deck.side),
        },
        modern=modern,
        require_side=False,
        pad_zones=pad_zones,
    )
    if deck.pretty_url:
        payload["ygoprodeck_url"] = YGOPRODECK_DECK_PAGE.format(pretty_url=deck.pretty_url)
    if deck.deck_num is not None:
        payload["ygoprodeck_deck_num"] = deck.deck_num
    return payload


def resolve_deck_for_archetype(
    archetype: str,
    *,
    period: BanlistPeriod | None = None,
    sources: dict[str, str] | None = None,
    cache: dict[str, dict[str, Any]] | None = None,
    modern: bool = False,
    pad_zones: bool = False,
    repo_root: Path | None = None,
) -> dict[str, Any] | None:
    if cache and archetype in cache:
        cached = dict(cache[archetype])
        cached["archetype"] = archetype
        return normalize_deck_dict(cached, modern=modern, require_side=False, pad_zones=pad_zones)

    source_map = sources or load_deck_sources()
    pretty_url = source_map.get(archetype)
    if pretty_url:
        scraped = scrape_deck_page(pretty_url)
        time.sleep(REQUEST_DELAY_SECONDS)
        return deck_to_dict(
            YgoProDeckDeck(
                name=scraped.name,
                archetype=archetype,
                main=scraped.main,
                extra=scraped.extra,
                side=scraped.side,
                source=scraped.source,
                pretty_url=scraped.pretty_url,
            ),
            archetype=archetype,
            modern=modern,
            pad_zones=pad_zones,
        )

    start_date = end_date = None
    if period is not None:
        start_date, end_date = period_date_range(period)

    api_deck = None
    if period is None or period.year >= 2019:
        api_deck = search_tournament_deck(
            archetype,
            start_date=start_date,
            end_date=end_date,
            deck_format="Tournament Meta Decks",
            repo_root=repo_root,
        )
    if api_deck is None:
        api_deck = search_tournament_deck(
            archetype,
            deck_format="Meta Decks",
            max_pages=20,
            repo_root=repo_root,
        )
    if api_deck is None or len(api_deck.main) < 40:
        return None
    return deck_to_dict(api_deck, archetype=archetype, modern=modern, pad_zones=pad_zones)


def build_deck_cache(
    *,
    repo_root: Path,
    output_path: Path = DEFAULT_CACHE_PATH,
    sources_path: Path = DEFAULT_SOURCES_PATH,
) -> dict[str, dict[str, Any]]:
    sources = load_deck_sources(sources_path)
    periods = banlist_periods()
    unique_archetypes: dict[str, BanlistPeriod] = {}
    for period in periods:
        for archetype in period.top5:
            if archetype not in unique_archetypes:
                unique_archetypes[archetype] = period

    cache: dict[str, dict[str, Any]] = {}
    for archetype, period in sorted(unique_archetypes.items()):
        modern = period.year >= 2017
        resolved = resolve_deck_for_archetype(
            archetype,
            period=period,
            sources=sources,
            cache=None,
            modern=modern,
            pad_zones=False,
            repo_root=repo_root,
        )
        if resolved is not None:
            cache[archetype] = resolved
        time.sleep(REQUEST_DELAY_SECONDS)

    save_deck_cache(cache, output_path)
    return cache
