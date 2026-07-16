"""Tests for :mod:`glyphive.codec` — the OCR-safe codec (g1).

Covers round-trip fidelity, single-char RS self-healing, over-budget failure
naming the failing line, and Crockford confusable decode aliases (I/L->1, O->0).
"""

import random

import pytest

from glyphive import codec
from glyphive.codec.g1 import ALPHABET, CodecError, crockford_decode, crockford_encode


g1 = codec.get("g1")


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
    lines = g1.encode(data)
    assert g1.decode(lines) == data


def test_roundtrip_line_width_boundary():
    # line_width default is 60 chars; exercise data exactly one full line wide.
    # bytes_per_line = (60 * 5) // 8 = 37 bytes.
    data = bytes(range(37))
    lines = g1.encode(data)
    assert g1.decode(lines) == data


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
    lines = g1.encode(data)

    idx = _first_data_line_index(lines)
    corrupted = list(lines)
    mutated = _mutate_one_payload_char(corrupted[idx])
    assert mutated != corrupted[idx]
    corrupted[idx] = mutated

    # The line's CRC now fails, marking it an erasure; RS repairs it.
    assert g1.decode(corrupted) == data


# --------------------------------------------------------------------------- #
# Corruption beyond the RS budget -> CodecError naming a line label
# --------------------------------------------------------------------------- #
def test_over_budget_corruption_raises_named():
    rng = random.Random(7)
    data = bytes(rng.randrange(256) for _ in range(400))
    # Small parity budget so a modest amount of damage exceeds it.
    lines = g1.encode(data, parity_ratio=0.02)

    corrupted = list(lines)
    # Wreck every data line's payload -> far beyond any RS budget.
    n_wrecked = 0
    for i, line in enumerate(corrupted):
        if line.startswith("L"):
            corrupted[i] = _mutate_one_payload_char(line)
            n_wrecked += 1
    assert n_wrecked > 0

    with pytest.raises(CodecError) as excinfo:
        g1.decode(corrupted)
    msg = str(excinfo.value)
    # Message must name a concrete line label (L##### or P#####).
    import re

    assert re.search(r"[LP]\d{5}", msg), msg


def test_codec_error_is_valueerror():
    assert issubclass(CodecError, ValueError)


def test_codec_registry_exposes_g1_and_direct_api():
    assert codec.names() == ["g1"]
    assert codec.available() == ["g1"]
    assert isinstance(codec.get("g1"), codec.G1Codec)
    payload = b"registry compatibility"
    assert g1.decode(codec.get("g1").encode(payload)) == payload


# --------------------------------------------------------------------------- #
# Structural frame parsing (Phase 3): tolerate OCR-inserted interior spaces
# --------------------------------------------------------------------------- #
from glyphive.codec.g1 import _parse_line, split_frame  # noqa: E402


def test_parse_line_tolerates_captured_ocr_transcript_line():
    # Captured Tesseract output: a genuine 3-token line with a spurious space
    # inserted inside the payload, splitting it into 4 whitespace tokens.
    ocr_line = (
        "L7KDX 8WRG2380000627WB10000000001FYWZQH4 "
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
    lines = g1.encode(data)
    line = next(l for l in lines if l.startswith("L"))
    label, payload, check = line.split()
    noisy_payload = payload[:10] + " " + payload[10:20] + " " + payload[20:]
    noisy_line = f"{label} {noisy_payload} {check}"
    assert len(noisy_line.split()) == 5  # label + 3 payload fragments + check

    parsed = _parse_line(noisy_line)
    assert parsed is not None
    assert parsed.payload == payload
    assert parsed.ok is True


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


def test_codec_registry_rejects_unknown_names():
    with pytest.raises(ValueError, match=r"unknown codec 'missing'.*g1"):
        codec.get("missing")


def test_codec_registry_rejects_duplicate_names():
    existing = dict(codec.Codec._registry)
    try:
        with pytest.raises(ValueError, match="duplicate codec name 'g1'"):
            type("DuplicateCodec", (codec.Codec,), {"name": "g1", "encode": lambda self, data, **options: [], "decode": lambda self, lines, **options: b""})
    finally:
        codec.Codec._registry.clear()
        codec.Codec._registry.update(existing)


# --------------------------------------------------------------------------- #
# Crockford confusable decode aliases (I/L -> 1, O -> 0)
# --------------------------------------------------------------------------- #
def test_crockford_decode_aliases_direct():
    # crockford_decode maps confusables before decoding. Encode a byte string,
    # then substitute the printed 1 -> I / L and 0 -> O and confirm the decode
    # is unaffected (the pure alias behavior, no framing involved).
    data = bytes([0x01, 0x08, 0x20, 0x00])
    encoded = crockford_encode(data)
    n = len(data)

    baseline = crockford_decode(encoded, n)
    assert baseline == data

    # Any '1' in the encoded text can be read as 'I' or 'L'; any '0' as 'O'.
    aliased_I = encoded.replace("1", "I")
    aliased_L = encoded.replace("1", "L")
    aliased_O = encoded.replace("0", "O")
    assert crockford_decode(aliased_I, n) == data
    assert crockford_decode(aliased_L, n) == data
    assert crockford_decode(aliased_O, n) == data
    # Lower-case aliases too (case-insensitive).
    assert crockford_decode(encoded.replace("0", "o"), n) == data


def test_confusable_substitution_in_framed_line_decodes():
    # Construct an encoded document, substitute a confusable in one data line's
    # payload, and confirm the document still decodes to the original bytes.
    #
    # NOTE on behavior: the per-line check field (CRC) is computed over the
    # *printed* payload characters, so substituting a printed '1' for 'I' in a
    # payload changes the recomputed CRC and the frame's own check REJECTS the
    # line (it becomes a CRC-failed erasure). The confusable alias then never
    # reaches crockford_decode for that line — instead RS repairs the erasure.
    # Either path is acceptable; we assert the document still decodes correctly
    # (whichever path the implementation takes).
    rng = random.Random(99)
    data = bytes(rng.randrange(256) for _ in range(300))
    lines = g1.encode(data)

    # Find a data line whose payload contains a canonical '1' or '0'.
    target = None
    for i, line in enumerate(lines):
        if not line.startswith("L"):
            continue
        _label, payload, _check = line.split()
        if "1" in payload or "0" in payload:
            target = i
            break
    assert target is not None, "no data line with a substitutable char found"

    label, payload, check = lines[target].split()
    if "1" in payload:
        payload = payload.replace("1", "I", 1)
    else:
        payload = payload.replace("0", "O", 1)
    corrupted = list(lines)
    corrupted[target] = f"{label} {payload} {check}"

    assert g1.decode(corrupted) == data
