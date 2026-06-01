"""Tests for EDOPro replay → human-duel JSON conversion."""

from __future__ import annotations

import lzma
import struct
from pathlib import Path

import pytest

from ygotrainingbot.replay_convert import (
    REPLAY_COMPRESSED,
    REPLAY_64BIT_DUELFLAG,
    REPLAY_NEWREPLAY,
    REPLAY_YRP1,
    REPLAY_YRPX,
    MSG_NEW_TURN,
    MSG_SELECT_IDLECMD,
    MSG_WIN,
    ReplayParseError,
    _decode_lzma_props,
    _find_lzma_offset,
    convert_replay_to_human_json,
    parse_replay_file,
)


def _utf16_name(text: str) -> bytes:
    encoded = text.encode("utf-16-le")
    buf = bytearray(40)
    buf[: len(encoded)] = encoded
    return bytes(buf)


def _build_yrpx_bytes() -> bytes:
    body = bytearray()
    body.extend(struct.pack("<I", 1))
    body.extend(_utf16_name("alice"))
    body.extend(struct.pack("<I", 1))
    body.extend(_utf16_name("bob"))
    body.extend(struct.pack("<Q", 0))  # duel_flags

    def packet(message: int, payload: bytes) -> None:
        body.append(message)
        body.extend(struct.pack("<I", len(payload)))
        body.extend(payload)

    packet(MSG_NEW_TURN, b"")
    packet(MSG_SELECT_IDLECMD, bytes([0]))
    packet(90, bytes([0]))  # MSG_DRAW
    packet(MSG_WIN, bytes([0, 1]))

    flag = REPLAY_COMPRESSED | REPLAY_NEWREPLAY | REPLAY_64BIT_DUELFLAG
    # EDOPro-style LZMA props: lc=3, lp=0, pb=2, dict=16MB -> prop0 = 0x5D
    props = bytes([0x5D, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00])
    decoded = _decode_lzma_props(props[:5])
    compressed = lzma.compress(
        bytes(body),
        format=lzma.FORMAT_RAW,
        filters=[
            {
                "id": lzma.FILTER_LZMA1,
                "lc": decoded["lc"],
                "lp": decoded["lp"],
                "pb": decoded["pb"],
                "dict_size": decoded["dict_size"],
            }
        ],
    )
    header = struct.pack("<6I", REPLAY_YRPX, 1, flag, 12345, len(body), 0) + props
    return header + compressed


def test_lzma_props_decode_matches_edopro() -> None:
    props = bytes([0x5D, 0x00, 0x00, 0x00, 0x01])
    decoded = _decode_lzma_props(props)
    assert decoded["lc"] == 3
    assert decoded["lp"] == 0
    assert decoded["pb"] == 2


def test_parse_minimal_yrpx(tmp_path: Path) -> None:
    path = tmp_path / "duel.yrpX"
    path.write_bytes(_build_yrpx_bytes())
    parsed = parse_replay_file(path)
    assert parsed.header.id == REPLAY_YRPX
    assert parsed.players[:2] == ["alice", "bob"]
    assert parsed.turn_count >= 1
    assert parsed.winner_player == 0
    assert len(parsed.packets) == 1


def test_convert_yrpx_to_human_json(tmp_path: Path) -> None:
    path = tmp_path / "duel.yrpX"
    path.write_bytes(_build_yrpx_bytes())
    payload = convert_replay_to_human_json(
        path,
        study_agent="alice",
        format_name="test-format",
    )
    assert payload["meta"]["study_agent"] == "alice"
    assert payload["meta"]["format"] == "test-format"
    assert payload["meta"]["player_a"] == "alice"
    assert len(payload["decisions"]) == 2
    labels = {d["selected_label"] for d in payload["decisions"]}
    assert "SELECT_IDLECMD" in labels
    assert "Draw card" in labels
    assert payload["result"]["winner"] == "alice"


def _build_compressed_yrpx_bytes() -> bytes:
    """EDOPro often prefixes ~40 bytes before the LZMA stream in saved replays."""

    plain = _build_yrpx_bytes()
    header_size = 32
    body = plain[header_size:]
    props = plain[24:29]
    decoded = _decode_lzma_props(props)
    filters = [
        {
            "id": lzma.FILTER_LZMA1,
            "lc": decoded["lc"],
            "lp": decoded["lp"],
            "pb": decoded["pb"],
            "dict_size": decoded["dict_size"],
        }
    ]
    compressed = lzma.compress(bytes(body), format=lzma.FORMAT_RAW, filters=filters)
    prefix = bytes(40)
    payload = prefix + compressed
    flag = REPLAY_COMPRESSED | REPLAY_NEWREPLAY | REPLAY_64BIT_DUELFLAG
    header = struct.pack("<6I", REPLAY_YRPX, 1, flag, 12345, len(body), 0) + props
    return header + payload


def test_parse_compressed_yrpx_with_lzma_prefix(tmp_path: Path) -> None:
    path = tmp_path / "saved.yrpX"
    path.write_bytes(_build_compressed_yrpx_bytes())
    raw = path.read_bytes()
    offset = _find_lzma_offset(raw[32:], raw[24:29], struct.unpack_from("<I", raw, 16)[0])
    assert offset == 40
    parsed = parse_replay_file(path)
    assert parsed.turn_count >= 1
    payload = convert_replay_to_human_json(path, study_agent="alice")
    assert len(payload["decisions"]) >= 2


def test_legacy_yrp_rejected(tmp_path: Path) -> None:
    replay = tmp_path / "old.yrp"
    replay.write_bytes(b"\x00" * 64)
    with pytest.raises(ReplayParseError):
        convert_replay_to_human_json(replay)


def test_misc_sample_yrp1_header(tmp_path: Path) -> None:
    sample = Path(__file__).resolve().parents[1] / "gateways" / "edopro-replay" / "misc-test.yrp"
    if not sample.is_file():
        pytest.skip("misc-test.yrp not present")
    parsed = parse_replay_file(sample)
    assert parsed.header.id == REPLAY_YRP1
    assert len(parsed.players) >= 2
    assert len(parsed.decks) == 2
    with pytest.raises(ReplayParseError):
        convert_replay_to_human_json(sample)
