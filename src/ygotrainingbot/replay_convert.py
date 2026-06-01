"""Convert EDOPro .yrp / .yrpX replay files to human-duel JSON for the dashboard."""

from __future__ import annotations

import json
import lzma
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPLAY_COMPRESSED = 0x1
REPLAY_TAG = 0x2
REPLAY_SINGLE_MODE = 0x8
REPLAY_NEWREPLAY = 0x20
REPLAY_HAND_TEST = 0x40
REPLAY_64BIT_DUELFLAG = 0x100
REPLAY_EXTENDED_HEADER = 0x200

REPLAY_YRP1 = 0x31707279
REPLAY_YRPX = 0x58707279

# Core message ids (ocgcore / EDOPro packet stream).
MSG_WIN = 5
MSG_SELECT_BATTLECMD = 10
MSG_SELECT_IDLECMD = 11
MSG_NEW_TURN = 40
MSG_AI_NAME = 163

PROMPT_MESSAGE_TYPES = frozenset(
    {
        MSG_SELECT_BATTLECMD,
        MSG_SELECT_IDLECMD,
        12,  # SELECT_EFFECTYN
        13,  # SELECT_YESNO
        14,  # SELECT_OPTION
        15,  # SELECT_CARD
        16,  # SELECT_CHAIN
        18,  # SELECT_PLACE
        19,  # SELECT_POSITION
        20,  # SELECT_TRIBUTE
        21,  # SORT_CHAIN
        22,  # SELECT_COUNTER
        23,  # SELECT_SUM
        24,  # SELECT_DISFIELD
        25,  # SORT_CARD
        26,  # SELECT_UNSELECT_CARD
        140,  # ANNOUNCE_RACE
        141,  # ANNOUNCE_ATTRIB
        142,  # ANNOUNCE_CARD
        143,  # ANNOUNCE_NUMBER
        131,  # ROCK_PAPER_SCISSORS
    }
)

# yrpX streams record client-visible outcomes (moves, summons, etc.), not only prompts.
ACTION_MESSAGE_TYPES: dict[int, tuple[str, list[str]]] = {
    50: ("Move card", ["tempo"]),
    53: ("Change position", ["tempo"]),
    54: ("Set card", ["set-spell", "set-monster"]),
    60: ("Normal summon", ["normal-summon"]),
    61: ("Monster summoned", ["normal-summon"]),
    62: ("Special summon", ["special-summon"]),
    63: ("Special summoned", ["special-summon"]),
    64: ("Flip summon", ["normal-summon"]),
    65: ("Flip summoned", ["normal-summon"]),
    70: ("Activate effect", ["activate"]),
    71: ("Chain link", ["chain"]),
    72: ("Resolve chain", ["chain"]),
    73: ("Chain solved", ["chain"]),
    74: ("Chain ended", ["chain"]),
    90: ("Draw card", ["draw"]),
    91: ("Deal damage", ["damage"]),
    92: ("Recover LP", ["recover"]),
    110: ("Declare attack", ["attack"]),
    111: ("Battle", ["attack", "damage"]),
    100: ("Pay LP cost", ["cost"]),
    101: ("Place counter", ["tempo"]),
}

MAX_YRPX_DECISIONS = 800

MESSAGE_LABELS: dict[int, str] = {
    MSG_SELECT_BATTLECMD: "SELECT_BATTLECMD",
    MSG_SELECT_IDLECMD: "SELECT_IDLECMD",
    MSG_WIN: "WIN",
    MSG_NEW_TURN: "NEW_TURN",
    12: "SELECT_EFFECTYN",
    13: "SELECT_YESNO",
    14: "SELECT_OPTION",
    15: "SELECT_CARD",
    16: "SELECT_CHAIN",
    18: "SELECT_PLACE",
    19: "SELECT_POSITION",
    20: "SELECT_TRIBUTE",
    21: "SORT_CHAIN",
    22: "SELECT_COUNTER",
    23: "SELECT_SUM",
    24: "SELECT_DISFIELD",
    25: "SORT_CARD",
    26: "SELECT_UNSELECT_CARD",
    140: "ANNOUNCE_RACE",
    141: "ANNOUNCE_ATTRIB",
    142: "ANNOUNCE_CARD",
    143: "ANNOUNCE_NUMBER",
    131: "ROCK_PAPER_SCISSORS",
    **{msg: label for msg, (label, _) in ACTION_MESSAGE_TYPES.items()},
}

DECISION_MESSAGE_TYPES: dict[int, tuple[str, list[str]]] = {
    **{
        msg: (MESSAGE_LABELS.get(msg, f"MSG_{msg}"), ["replay", "prompt"])
        for msg in PROMPT_MESSAGE_TYPES
    },
    **ACTION_MESSAGE_TYPES,
}


@dataclass
class ReplayHeader:
    id: int
    version: int
    flag: int
    seed: int
    datasize: int
    hash_value: int
    props: bytes
    header_size: int = 32
    extended_seed: tuple[int, int, int, int] | None = None


@dataclass
class ReplayDeck:
    main: list[int] = field(default_factory=list)
    extra: list[int] = field(default_factory=list)


@dataclass
class ParsedReplay:
    header: ReplayHeader
    players: list[str]
    params: dict[str, Any]
    decks: list[ReplayDeck]
    responses: list[bytes]
    packets: list[tuple[int, bytes]]
    all_packets: list[tuple[int, bytes]] = field(default_factory=list)
    turn_count: int = 0
    winner_player: int | None = None
    win_reason: int | None = None


class ReplayParseError(ValueError):
    pass


class BufferReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos

    def read(self, size: int) -> bytes:
        if self._pos + size > len(self._data):
            raise ReplayParseError("unexpected end of replay data")
        chunk = self._data[self._pos : self._pos + size]
        self._pos += size
        return chunk

    def read_u8(self) -> int:
        return self.read(1)[0]

    def read_u16(self) -> int:
        return struct.unpack_from("<H", self.read(2))[0]

    def read_u32(self) -> int:
        return struct.unpack_from("<I", self.read(4))[0]

    def read_u64(self) -> int:
        return struct.unpack_from("<Q", self.read(8))[0]

    def read_name(self) -> str:
        raw = self.read(40)
        text = raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0].strip()
        return text or "player"


def _decode_lzma_props(props: bytes) -> dict[str, int]:
    """Decode the 5-byte LZMA props block used by YGOPro / EDOPro (see LzmaLib.h)."""

    if len(props) < 5:
        raise ReplayParseError("replay LZMA props are too short")
    prop0 = props[0]
    lc = prop0 % 9
    remainder = prop0 // 9
    lp = remainder % 5
    pb = remainder // 5
    dict_size = struct.unpack_from("<I", props, 1)[0]
    if dict_size < 4096:
        dict_size = 1 << 24
    return {"lc": lc, "lp": lp, "pb": pb, "dict_size": dict_size}


def _lzma_filters(props: bytes) -> list[dict[str, int]]:
    decoded = _decode_lzma_props(props[:5].ljust(5, b"\x00")[:5])
    return [
        {
            "id": lzma.FILTER_LZMA1,
            "lc": decoded["lc"],
            "lp": decoded["lp"],
            "pb": decoded["pb"],
            "dict_size": decoded["dict_size"],
        }
    ]


def _decompress_lzma_at(
    compressed: bytes,
    props: bytes,
    expected_size: int,
    *,
    offset: int = 0,
) -> bytes:
    """Decompress one LZMA1 stream (no end-of-stream marker; size from header)."""

    filters = _lzma_filters(props)
    decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
    if expected_size > 0:
        body = decompressor.decompress(compressed[offset:], max_length=expected_size)
        if len(body) < expected_size:
            raise lzma.LZMAError(
                f"short output ({len(body)} bytes, expected {expected_size})"
            )
        return body[:expected_size]
    return decompressor.decompress(compressed[offset:])


def _find_lzma_offset(compressed: bytes, props: bytes, expected_size: int) -> int:
    """Locate the LZMA stream inside a replay payload (EDOPro may prefix ~40 bytes)."""

    if not compressed:
        raise ReplayParseError("replay has no compressed payload")

    scan_limit = min(128, len(compressed))
    candidates = [0]
    if expected_size > 0:
        candidates.extend(range(1, scan_limit))

    for offset in candidates:
        try:
            _decompress_lzma_at(
                compressed,
                props,
                expected_size,
                offset=offset,
            )
            return offset
        except lzma.LZMAError:
            continue

    raise ReplayParseError("failed to locate LZMA stream in replay payload")


def _decompress_lzma(compressed: bytes, props: bytes, expected_size: int) -> bytes:
    """Decompress replay payload the same way as EDOPro's LzmaUncompress."""

    if not compressed:
        raise ReplayParseError("replay has no compressed payload")

    lzma_props = props[:5].ljust(5, b"\x00")[:5]
    errors: list[str] = []

    try:
        offset = _find_lzma_offset(compressed, lzma_props, expected_size)
        return _decompress_lzma_at(
            compressed,
            lzma_props,
            expected_size,
            offset=offset,
        )
    except (lzma.LZMAError, ReplayParseError) as exc:
        errors.append(f"raw: {exc}")

    if expected_size > 0:
        alone_prefix = lzma_props + struct.pack("<Q", expected_size)
        for offset in (0, 40):
            if offset >= len(compressed):
                continue
            try:
                return lzma.decompress(
                    alone_prefix + compressed[offset:],
                    format=lzma.FORMAT_ALONE,
                )
            except lzma.LZMAError as exc:
                errors.append(f"alone@{offset}: {exc}")

    raise ReplayParseError(
        "failed to decompress replay"
        + (f" ({'; '.join(errors)})" if errors else "")
    )


def parse_replay_file(path: Path) -> ParsedReplay:
    raw = path.read_bytes()
    if len(raw) < 32:
        raise ReplayParseError(f"file too small to be a replay: {path}")

    header = _parse_header(raw)
    body_offset = header.header_size
    compressed = raw[body_offset:]
    if header.flag & REPLAY_COMPRESSED:
        body = _decompress_lzma(compressed, header.props, header.datasize)
    else:
        body = compressed

    reader = BufferReader(body)
    players, home_count, away_count = _parse_names(reader, header)
    params = _parse_params(reader, header)
    decks: list[ReplayDeck] = []
    responses: list[bytes] = []
    packets: list[tuple[int, bytes]] = []
    all_packets: list[tuple[int, bytes]] = []
    turn_count = 0
    winner_player: int | None = None
    win_reason: int | None = None

    if header.id == REPLAY_YRP1:
        decks = _parse_decks(reader, header, home_count + away_count)
        responses = _parse_responses(reader)
    elif header.id == REPLAY_YRPX:
        all_packets, packets, turn_count, winner_player, win_reason = _parse_packet_stream(
            reader, players
        )
    else:
        raise ReplayParseError(
            f"unsupported replay id 0x{header.id:08x} (expected .yrp or .yrpX)"
        )

    return ParsedReplay(
        header=header,
        players=players,
        params=params,
        decks=decks,
        responses=responses,
        packets=packets,
        all_packets=all_packets,
        turn_count=turn_count,
        winner_player=winner_player,
        win_reason=win_reason,
    )


def _parse_header(raw: bytes) -> ReplayHeader:
    base = struct.unpack_from("<6I", raw, 0)
    props = raw[24:32]
    header_size = 32
    extended_seed = None
    flag = base[2]
    if flag & REPLAY_EXTENDED_HEADER:
        if len(raw) < 72:
            raise ReplayParseError("extended replay header is truncated")
        header_size = 72
        extended_seed = struct.unpack_from("<4Q", raw, 32)

    return ReplayHeader(
        id=base[0],
        version=base[1],
        flag=flag,
        seed=base[3],
        datasize=base[4],
        hash_value=base[5],
        props=props,
        header_size=header_size,
        extended_seed=extended_seed,
    )


def _parse_names(reader: BufferReader, header: ReplayHeader) -> tuple[list[str], int, int]:
    players: list[str] = []
    home_count = 0
    away_count = 0

    if header.flag & REPLAY_SINGLE_MODE:
        players.append(reader.read_name())
        players.append(reader.read_name())
        return players, 1, 1

    def read_side(count_out: list[int]) -> None:
        if header.flag & REPLAY_NEWREPLAY:
            count = reader.read_u32()
        elif header.flag & REPLAY_TAG:
            count = 2
        else:
            count = 1
        count_out.append(count)
        for _ in range(count):
            players.append(reader.read_name())

    home: list[int] = []
    away: list[int] = []
    read_side(home)
    read_side(away)
    return players, home[0], away[0]


def _parse_params(reader: BufferReader, header: ReplayHeader) -> dict[str, Any]:
    params: dict[str, Any] = {
        "start_lp": 8000,
        "start_hand": 5,
        "draw_count": 1,
        "duel_flags": 0,
    }
    if header.id == REPLAY_YRP1:
        params["start_lp"] = reader.read_u32()
        params["start_hand"] = reader.read_u32()
        params["draw_count"] = reader.read_u32()
    if header.flag & REPLAY_64BIT_DUELFLAG:
        params["duel_flags"] = reader.read_u64()
    else:
        params["duel_flags"] = reader.read_u32()
    if header.flag & REPLAY_SINGLE_MODE and header.id == REPLAY_YRP1:
        slen = reader.read_u16()
        reader.read(slen)
    return params


def _parse_decks(reader: BufferReader, header: ReplayHeader, player_count: int) -> list[ReplayDeck]:
    if header.id != REPLAY_YRP1:
        return []
    if header.flag & REPLAY_SINGLE_MODE and not (header.flag & REPLAY_HAND_TEST):
        return []

    decks: list[ReplayDeck] = []
    for _ in range(player_count):
        main_count = reader.read_u32()
        main = [reader.read_u32() for _ in range(main_count)]
        extra_count = reader.read_u32()
        extra = [reader.read_u32() for _ in range(extra_count)]
        decks.append(ReplayDeck(main=main, extra=extra))

    if header.flag & REPLAY_NEWREPLAY and not (header.flag & REPLAY_HAND_TEST):
        rules = reader.read_u32()
        for _ in range(rules):
            reader.read_u32()
    return decks


def _parse_responses(reader: BufferReader) -> list[bytes]:
    responses: list[bytes] = []
    while reader.remaining > 0:
        length = reader.read_u8()
        if length == 0:
            continue
        responses.append(reader.read(length))
    return responses


def _parse_packet_stream(
    reader: BufferReader,
    players: list[str],
) -> tuple[list[tuple[int, bytes]], list[tuple[int, bytes]], int, int | None, int | None]:
    all_packets: list[tuple[int, bytes]] = []
    prompt_packets: list[tuple[int, bytes]] = []
    turn_count = 0
    winner_player: int | None = None
    win_reason: int | None = None

    while reader.remaining > 0:
        message = reader.read_u8()
        if reader.remaining < 4:
            break
        length = reader.read_u32()
        if length > reader.remaining:
            break
        payload = reader.read(length)
        all_packets.append((message, payload))

        if message == MSG_AI_NAME and len(players) >= 2:
            if len(payload) >= 2:
                name_len = struct.unpack_from("<H", payload, 0)[0]
                name_bytes = payload[2 : 2 + name_len]
                players[1] = name_bytes.decode("utf-8", errors="replace") or players[1]
            continue
        if message == MSG_NEW_TURN:
            turn_count += 1
        if message == MSG_WIN and len(payload) >= 2:
            winner_player = payload[0]
            win_reason = payload[1]
        if message in PROMPT_MESSAGE_TYPES:
            prompt_packets.append((message, payload))
    return all_packets, prompt_packets, turn_count, winner_player, win_reason


def _player_id(players: list[str], index: int) -> str:
    if 0 <= index < len(players):
        return players[index]
    return f"player-{index}"


def _pick_study_agent(players: list[str], explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    for name in players:
        if name and not name.startswith("[AI]"):
            return name
    return players[0] if players else "player-0"


def _player_index_from_payload(payload: bytes) -> int | None:
    if payload and payload[0] in (0, 1):
        return int(payload[0])
    return None


def _yrpx_to_human_payload(
    parsed: ParsedReplay,
    *,
    study_agent: str | None,
    format_name: str | None,
    source_path: Path,
) -> dict[str, Any]:
    players = parsed.players or ["player-0", "player-1"]
    player_a = players[0]
    player_b = players[1] if len(players) > 1 else "player-1"
    study = _pick_study_agent(players, study_agent)

    decisions: list[dict[str, Any]] = []
    duel_turn = 1
    decision_index = 0
    active_player = study

    stream = parsed.all_packets or parsed.packets
    for message_type, payload in stream:
        if message_type == MSG_NEW_TURN:
            duel_turn += 1
            continue
        if message_type == MSG_AI_NAME:
            continue
        if message_type not in DECISION_MESSAGE_TYPES:
            continue

        label, tags = DECISION_MESSAGE_TYPES[message_type]
        team = _player_index_from_payload(payload)
        if team is not None:
            active_player = _player_id(players, team)
        agent = active_player

        decision_index += 1
        decisions.append(
            {
                "turn": duel_turn,
                "agent": agent,
                "summary": f"Replay — {label}",
                "selected_action": f"replay-{message_type}-{decision_index}",
                "selected_label": label,
                "selected_tags": list(tags),
                "evaluation": f"msg={message_type}; payload_bytes={len(payload)}",
            }
        )
        if len(decisions) >= MAX_YRPX_DECISIONS:
            break

    if not decisions and (
        parsed.turn_count > 0
        or parsed.winner_player is not None
        or len(stream) > 0
    ):
        decisions.append(
            {
                "turn": max(parsed.turn_count, 1),
                "agent": study,
                "summary": "Replay imported (no discrete actions parsed from stream)",
                "selected_action": "replay-summary",
                "selected_label": "Replay summary",
                "selected_tags": ["replay"],
                "evaluation": f"packets={len(stream)}; turns={parsed.turn_count}",
            }
        )

    winner = loser = None
    if parsed.winner_player is not None:
        winner = _player_id(players, parsed.winner_player)
        loser = player_b if winner == player_a else player_a

    return {
        "meta": {
            "format": format_name or "unknown",
            "source": "human",
            "source_replay": str(source_path.name),
            "replay_kind": "yrpX",
            "player_a": player_a,
            "player_b": player_b,
            "study_agent": study,
            "notes": "Auto-converted from EDOPro .yrpX. Set study_agent to your in-game name.",
        },
        "result": {
            "winner": winner,
            "loser": loser,
            "turns": max(parsed.turn_count, duel_turn - 1, 1),
            "win_reason": parsed.win_reason,
        },
        "decisions": decisions,
    }


def convert_replay_bytes_to_human_json(
    data: bytes,
    *,
    source_name: str,
    study_agent: str | None = None,
    format_name: str | None = None,
) -> dict[str, Any]:
    """Convert in-memory replay bytes (for HTTP uploads)."""

    suffix = Path(source_name).suffix.lower() or ".yrpX"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        temp_path = Path(handle.name)
    try:
        return convert_replay_to_human_json(
            temp_path,
            study_agent=study_agent,
            format_name=format_name,
        )
    finally:
        temp_path.unlink(missing_ok=True)


def convert_replay_to_human_json(
    replay_path: Path,
    *,
    study_agent: str | None = None,
    format_name: str | None = None,
) -> dict[str, Any]:
    """Return a human-duel JSON payload from *replay_path*."""

    path = replay_path.resolve()
    if not path.is_file():
        raise ReplayParseError(f"replay file not found: {path}")

    parsed = parse_replay_file(path)
    suffix = path.suffix.lower()
    if parsed.header.id == REPLAY_YRPX or suffix == ".yrpx":
        return _yrpx_to_human_payload(
            parsed,
            study_agent=study_agent,
            format_name=format_name,
            source_path=path,
        )

    if parsed.header.id == REPLAY_YRP1 or suffix == ".yrp":
        raise ReplayParseError(
            "Legacy .yrp replays cannot be converted to decisions yet. "
            "Open the duel in EDOPro and re-save as .yrpX, or point convert-edopro-replay at your replay/ folder."
        )

    raise ReplayParseError(f"unsupported replay format: {path.name}")


def write_converted_replay(
    replay_path: Path,
    output_path: Path,
    *,
    study_agent: str | None = None,
    format_name: str | None = None,
) -> Path:
    payload = convert_replay_to_human_json(
        replay_path,
        study_agent=study_agent,
        format_name=format_name,
    )
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def prepare_human_upload_files(
    files: list[tuple[str, bytes]],
    *,
    study_agent: str | None = None,
    format_name: str | None = None,
) -> tuple[list[tuple[str, str]], list[dict[str, Any]], int]:
    """Normalize uploads: convert .yrp/.yrpX to JSON text for catalog import."""

    prepared: list[tuple[str, str]] = []
    errors: list[dict[str, Any]] = []
    converted = 0

    for filename, raw in files:
        label = Path(filename).name or "upload"
        lower = label.lower()
        if lower.endswith((".yrp", ".yrpx")):
            try:
                payload = convert_replay_bytes_to_human_json(
                    raw,
                    source_name=label,
                    study_agent=study_agent,
                    format_name=format_name,
                )
            except ReplayParseError as exc:
                errors.append({"path": label, "error": str(exc)})
                continue
            json_name = f"{Path(label).stem}.human.json"
            prepared.append((json_name, json.dumps(payload, indent=2, sort_keys=True) + "\n"))
            converted += 1
            continue

        if not lower.endswith(".json"):
            label = f"{label}.json"

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors.append({"path": label, "error": str(exc)})
            continue
        prepared.append((label, text))

    return prepared, errors, converted


REPLAY_SUFFIXES = (".yrpX", ".yrpx", ".yrp")


def iter_replay_files(path: Path) -> list[Path]:
    """Return replay files under *path* (single file or directory scan)."""

    resolved = path.resolve()
    if resolved.is_file():
        return [resolved]
    if not resolved.is_dir():
        raise ReplayParseError(f"replay path not found: {path}")

    files = [
        candidate
        for candidate in resolved.iterdir()
        if candidate.is_file() and candidate.suffix.lower() in REPLAY_SUFFIXES
    ]
    return sorted(files, key=lambda item: item.name.lower())


def convert_replays_in_path(
    path: Path,
    *,
    output_dir: Path | None = None,
    study_agent: str | None = None,
    format_name: str | None = None,
) -> tuple[list[Path], list[dict[str, str]]]:
    """Convert every .yrp / .yrpX in a directory (or one file) to .human.json."""

    written: list[Path] = []
    errors: list[dict[str, str]] = []
    for replay_path in iter_replay_files(path):
        out = (output_dir or replay_path.parent) / f"{replay_path.stem}.human.json"
        try:
            written.append(
                write_converted_replay(
                    replay_path,
                    out,
                    study_agent=study_agent,
                    format_name=format_name,
                )
            )
        except ReplayParseError as exc:
            errors.append({"path": str(replay_path), "error": str(exc)})
    return written, errors
