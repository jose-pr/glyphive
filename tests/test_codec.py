"""Tests for :mod:`glyphive.codec` — the OCR-safe codec (base16c-crc16-rs).

Covers round-trip fidelity, single-char RS self-healing, over-budget failure
naming the failing line, and rejection of out-of-alphabet characters (Design
Q2: no confusable aliases beyond case-folding -- a rejected char is a CRC
erasure, recoverable by RS; the prior Crockford aliases silently corrupted
data via ``Q``->``O``->``0`` and ``J``->``I``->``1`` and are gone).
"""

import io
import random

import pytest

from glyphive import codec
from glyphive.codec.base16c import (
    ALPHABET,
    CodecError,
    _encoding_shape,
    encoded_line_count,
    nibble_decode,
    nibble_encode,
)


base16c = codec.get("base16c-crc16-rs")


# --------------------------------------------------------------------------- #
# Round-trip
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "size",
    [0, 1, 2, 59, 60, 61, 255, 256, 1000, 4096],
)
def test_roundtrip_sizes(size):
    rng = random.Random(1234 + size)
    data = bytes(rng.randrange(256) for _ in range(size))
    lines = base16c.encode(data)
    assert base16c.decode(lines) == data


def test_roundtrip_line_width_boundary():
    # line_width default is 60 chars; exercise data exactly one full line wide.
    # bytes_per_line = (60 * 4) // 8 = 30 bytes (4 bits/char, the 16-char alphabet).
    data = bytes(range(30))
    lines = base16c.encode(data)
    assert base16c.decode(lines) == data


@pytest.mark.parametrize("size", [0, 1, 29, 30, 31, 255, 4096])
def test_streaming_encode_is_line_identical(size):
    data = bytes((index * 31) % 256 for index in range(size))
    expected = base16c.encode(data)
    actual = list(base16c.iter_encode(io.BytesIO(data), len(data)))
    assert actual == expected
    assert encoded_line_count(len(data)) == len(expected)


def test_streaming_encode_rejects_truncated_and_grown_source():
    with pytest.raises(ValueError, match="truncated"):
        list(base16c.iter_encode(io.BytesIO(b"short"), 6))
    with pytest.raises(ValueError, match="grew"):
        list(base16c.iter_encode(io.BytesIO(b"extra"), 4))


def test_spooled_decode_matches_one_shot_and_repairs_erasures():
    data = bytes((index * 17) % 256 for index in range(4096))
    lines = base16c.encode(data)
    damaged = list(lines)
    line_index = _first_data_line_index(damaged)
    damaged[line_index] = _mutate_one_payload_char(damaged[line_index])
    encoded = io.BytesIO("\n".join(damaged).encode("utf-8") + b"\n")
    restored = io.BytesIO()
    base16c.decode_spool(encoded, restored)
    assert restored.getvalue() == data


@pytest.mark.parametrize(
    ("size", "nsym", "blocks", "parity_bytes", "total_lines"),
    [
        (100, 13, 1, 13, 5),
        (1_000, 24, 5, 120, 38),
        (10_000, 27, 44, 1_188, 374),
        (100_000, 27, 439, 11_853, 3_730),
        (1_000_000, 27, 4_386, 118_422, 37_282),
    ],
)
def test_default_parity_is_global_twelve_percent(
    size, nsym, blocks, parity_bytes, total_lines
):
    shape = _encoding_shape(size, 60, 0.12)
    assert shape[2:4] == (nsym, blocks)
    assert blocks * nsym == parity_bytes
    assert sum(shape[-2:]) == total_lines
    assert abs(parity_bytes / (size + 8) - 0.12) < 0.002


# --------------------------------------------------------------------------- #
# Single wrong char in a data line -> RS heals it
# --------------------------------------------------------------------------- #
def _first_data_line_index(lines):
    for i, line in enumerate(lines):
        if line.startswith("L"):
            return i
    raise AssertionError("no data line found")


def _mutate_one_payload_char(line):
    """Return ``line`` with exactly one payload char changed to a different
    valid Crockford character."""
    label, payload, check = line.split()
    # Change the first payload char to a different alphabet char.
    orig = payload[0]
    replacement = next(c for c in ALPHABET if c != orig)
    new_payload = replacement + payload[1:]
    return f"{label} {new_payload} {check}"


def test_single_char_error_self_heals():
    rng = random.Random(42)
    data = bytes(rng.randrange(256) for _ in range(500))
    lines = base16c.encode(data)

    idx = _first_data_line_index(lines)
    corrupted = list(lines)
    mutated = _mutate_one_payload_char(corrupted[idx])
    assert mutated != corrupted[idx]
    corrupted[idx] = mutated

    # The line's CRC now fails, marking it an erasure; RS repairs it.
    assert base16c.decode(corrupted) == data


# --------------------------------------------------------------------------- #
# Corruption beyond the RS budget -> CodecError naming a line label
# --------------------------------------------------------------------------- #
def test_over_budget_corruption_raises_named():
    rng = random.Random(7)
    data = bytes(rng.randrange(256) for _ in range(400))
    # Small parity budget so a modest amount of damage exceeds it.
    lines = base16c.encode(data, parity_ratio=0.02)

    corrupted = list(lines)
    # Wreck every data line's payload -> far beyond any RS budget.
    n_wrecked = 0
    for i, line in enumerate(corrupted):
        if line.startswith("L"):
            corrupted[i] = _mutate_one_payload_char(line)
            n_wrecked += 1
    assert n_wrecked > 0

    with pytest.raises(CodecError) as excinfo:
        base16c.decode(corrupted)
    msg = str(excinfo.value)
    # Message must name a concrete line label (L##### or P#####).
    import re

    assert re.search(r"[LP]\d{5}", msg), msg


def test_codec_error_is_valueerror():
    assert issubclass(CodecError, ValueError)


def test_codec_registry_exposes_g1_and_direct_api():
    assert codec.names() == ["base16c-crc16-rs"]
    assert codec.available() == ["base16c-crc16-rs"]
    assert isinstance(codec.get("base16c-crc16-rs"), codec.Base16CCodec)
    payload = b"registry compatibility"
    assert base16c.decode(codec.get("base16c-crc16-rs").encode(payload)) == payload


# --------------------------------------------------------------------------- #
# Structural frame parsing: tolerate OCR-inserted interior spaces
# --------------------------------------------------------------------------- #
from glyphive.codec.base16c import _parse_line, split_frame  # noqa: E402


def test_parse_line_tolerates_captured_ocr_transcript_line():
    # Captured Tesseract output re-pinned for the new 5-char index token --
    # INDEX_WIDTH grew from 4 to 5 alongside the alphabet's bit-width
    # change): a genuine 3-token line with a spurious space inserted inside the
    # payload, splitting it into 4 whitespace tokens.
    ocr_line = (
        "LMYCVH 8WRG2380000627WB10000000001FYWZQH4 "
        "6F1IWO0C6DJ64R320015D1J4QP90 #1RBN"
    )
    parsed = _parse_line(ocr_line)
    assert parsed is not None
    assert parsed.kind == "L"
    assert " " not in parsed.payload


def test_parse_line_tolerates_two_interior_spaces():
    # Build a real line, then punch two spurious interior spaces into its
    # payload the way Tesseract does, and confirm it still parses and its CRC
    # still validates (the CRC is recomputed over the rejoined payload).
    data = bytes(range(40))
    lines = base16c.encode(data)
    line = next(l for l in lines if l.startswith("L"))
    label, payload, check = line.split()
    noisy_payload = payload[:10] + " " + payload[10:20] + " " + payload[20:]
    noisy_line = f"{label} {noisy_payload} {check}"
    assert len(noisy_line.split()) == 5  # label + 3 payload fragments + check

    parsed = _parse_line(noisy_line)
    assert parsed is not None
    assert parsed.payload == payload
    assert parsed.ok is True


def test_parse_line_accepts_compact_frame_with_valid_crc():
    line = next(line for line in base16c.encode(b"compact frame") if line.startswith("L"))
    compact = line.replace(" ", "")

    parsed = _parse_line(compact)

    assert parsed is not None
    assert parsed.ok is True


def test_parse_line_keeps_corrupted_compact_frame_as_crc_erasure():
    line = next(line for line in base16c.encode(b"compact frame") if line.startswith("L"))
    compact = line.replace(" ", "")
    payload_start = 6
    replacement = "A" if compact[payload_start] != "A" else "B"
    corrupted = compact[:payload_start] + replacement + compact[payload_start + 1:]

    parsed = _parse_line(corrupted)

    assert parsed is not None
    assert parsed.ok is False


def test_parse_line_rejects_page_footer():
    # The page footer starts with "P" (like a parity-line kind) and has 3
    # tokens, but its last token is not a "#check" field -- must not be
    # mistaken for a real frame.
    footer = "PAGE 1/1 sha256=ea5b07a93a037a43"
    assert split_frame(footer) is None
    assert _parse_line(footer) is None


def test_split_frame_anchors_label_first_check_last():
    assert split_frame("L7KDX AB CD #1RBN") == ("L7KDX", "ABCD", "#1RBN")
    assert split_frame("no check field here") is None


# --------------------------------------------------------------------------- #
# Index encoding never renders as a uniform run with the 16-char alphabet /
# 5-char index token.
# --------------------------------------------------------------------------- #
from glyphive.codec.base16c import decode_index, encode_index  # noqa: E402


def test_encode_index_never_renders_uniform_run():
    # A run of identical glyphs is the single worst OCR target on the page
    # (engines reliably insert phantom characters into it) -- the masked
    # index token exists precisely to avoid this. Check every index actually
    # used in practice, not just a few samples.
    for idx in range(0, 5001):
        token = encode_index(idx)
        assert len(set(token)) > 1, f"idx {idx} rendered as uniform run {token!r}"


def test_encode_decode_index_roundtrip():
    for idx in (0, 1, 42, 5000, 99999, 1_048_575):
        token = encode_index(idx)
        assert decode_index(token) == idx


def test_codec_registry_rejects_unknown_names():
    with pytest.raises(ValueError, match=r"unknown codec 'missing'.*base16c-crc16-rs"):
        codec.get("missing")


def test_codec_registry_rejects_duplicate_names():
    existing = dict(codec.Codec._registry)
    try:
        with pytest.raises(ValueError, match="duplicate codec name 'base16c-crc16-rs'"):
            type("DuplicateCodec", (codec.Codec,), {"name": "base16c-crc16-rs", "encode": lambda self, data, **options: [], "decode": lambda self, lines, **options: b""})
    finally:
        codec.Codec._registry.clear()
        codec.Codec._registry.update(existing)


# --------------------------------------------------------------------------- #
# Out-of-alphabet characters are rejected, not aliased.
# --------------------------------------------------------------------------- #
def test_nibble_decode_case_insensitive_but_no_confusable_aliases():
    # nibble_decode is case-insensitive over the 16-char alphabet itself, but
    # applies NO confusable aliasing: the excluded confusable characters this
    # alphabet was chosen to avoid (0, 1, O, I, Q, J, ...) must raise rather
    # than silently resolve to some other value. This is the direct fix for
    # the prior alphabet's silent-corruption bug (Q->O->alias->0, J->I->alias->1).
    data = bytes([0x01, 0x08, 0x20, 0x00])
    encoded = nibble_encode(data)
    n = len(data)

    baseline = nibble_decode(encoded, n)
    assert baseline == data

    # Lower-case of an in-alphabet letter still decodes fine.
    assert nibble_decode(encoded.lower(), n) == data

    # Every excluded confusable character is rejected outright -- no alias.
    for excluded in ("0", "1", "O", "o", "I", "i", "Q", "q", "J", "j"):
        assert excluded not in ALPHABET
        noisy = excluded + encoded[1:]
        with pytest.raises(ValueError):
            nibble_decode(noisy, n)


def test_excluded_confusable_in_framed_line_repairs_via_rs():
    # Simulate an OCR misread that turns a printed alphabet character into an
    # EXCLUDED confusable (e.g. '0', which looks like the excluded 'O'). With
    # no alias in play, the line's own check field (computed over the printed
    # payload) simply fails, the line becomes a CRC erasure, and RS repairs it
    # exactly like any other single-line error -- the same recovery path as
    # test_single_char_error_self_heals, just via an out-of-alphabet char
    # instead of an in-alphabet one.
    rng = random.Random(99)
    data = bytes(rng.randrange(256) for _ in range(300))
    lines = base16c.encode(data)

    idx = _first_data_line_index(lines)
    label, payload, check = lines[idx].split()
    assert "0" not in ALPHABET
    noisy_payload = "0" + payload[1:]
    corrupted = list(lines)
    corrupted[idx] = f"{label} {noisy_payload} {check}"

    assert base16c.decode(corrupted) == data
