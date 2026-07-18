"""Tests for :mod:`glyphive.codec` — the OCR-safe codec (base16g-crc16-rs).

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


base16c = codec.get("base16g-crc16-rs")


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


def _find_non_last_data_line(lines):
    """Return the index in ``lines`` of a data line that is not the last one."""
    data_positions = [i for i, line in enumerate(lines) if line.startswith("L")]
    assert len(data_positions) >= 3, "need multiple data lines for this test"
    return data_positions[1]  # a middle line, definitely not the last


def _pad_payload(line, extra):
    """Return ``line`` with ``extra`` alphabet chars appended to its payload.

    Widens the line beyond the modal width and (because the check no longer
    matches) breaks its CRC -- exactly the OCR class where a substitution also
    merged/duplicated a character (real-recovery finding #4).
    """
    label, payload, check = line.split()
    return f"{label} {payload + ALPHABET[0] * extra} {check}"


def _truncate_payload(line, fewer):
    """Return ``line`` with ``fewer`` chars removed from its payload."""
    label, payload, check = line.split()
    return f"{label} {payload[:-fewer]} {check}"


def test_wrong_length_line_does_not_poison_global_byte_width():
    """One over/under-length line must not change bytes_per_line for the rest.

    Real-recovery finding #4: ``decode`` derived the stream-wide byte width from
    ``max()`` over every parsed line, so a single OCR-corrupted line whose
    payload came out a couple of characters too long widened the geometry for
    every other, perfectly good line and broke the whole RS decode. The width
    now comes from the modal payload length, and the off-width line is forced
    into an erasure RS repairs.
    """
    rng = random.Random(1234)
    data = bytes(rng.randrange(256) for _ in range(500))
    lines = base16c.encode(data)

    for corrupt in (_pad_payload, _truncate_payload):
        corrupted = list(lines)
        idx = _find_non_last_data_line(corrupted)
        corrupted[idx] = corrupt(corrupted[idx], 2)
        assert corrupted[idx] != lines[idx]
        # Every other line is untouched and perfectly good; the one wrong-length
        # line is an erasure the interleaved RS repairs.
        assert base16c.decode(corrupted) == data


def test_wrong_width_line_with_valid_crc_still_decodes(monkeypatch):
    """A longer-than-modal line whose CRC coincidentally passes still decodes (F3).

    The modal-width check forces such a line's stored index entry to ``ok=False``
    (`_assemble_to_spool` now honors that stored flag, not just the re-parsed
    CRC), so it becomes a known RS erasure instead of feeding shifted bytes to
    RS as an uncorrected blind error. This exercises the honored-flag path: with
    ``_assemble_to_spool`` consulting the stored flag, the wrong-width line is an
    erasure and RS repairs the document.
    """
    from glyphive.codec.base16c import _check_chars

    rng = random.Random(99)
    data = bytes(rng.randrange(256) for _ in range(500))
    lines = base16c.encode(data)

    idx = _find_non_last_data_line(lines)
    corrupted = list(lines)
    label, payload, _check = corrupted[idx].split()
    idx_token = label[1:]
    wider = payload + ALPHABET[0] * 2  # 2 chars too wide, off the modal width
    # Recompute the CRC so this wrong-width line PASSES its own check.
    corrupted[idx] = f"{label} {wider} #{_check_chars(idx_token, wider)}"

    # It decodes byte-for-byte: the wrong-width line is treated as an erasure
    # (via the stored ok=False the modal-width check set) and RS repairs it.
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


_BUILTIN_CODECS = [
    "base16-crc16-rs", "base16g-crc16-rs", "base32-crc16-rs", "base32c-crc16-rs",
    "base32g-crc16-rs", "base64-crc16-rs", "base64g-crc16-rs", "base85-crc16-rs",
    "base8g-crc16-rs", "basemaxg-crc16-rs", "z85-crc16-rs",
]


def test_codec_registry_exposes_g1_and_direct_api():
    # base16c is the default; base8/base32g/base64 are the denser family.
    assert codec.names() == _BUILTIN_CODECS
    assert codec.available() == _BUILTIN_CODECS
    assert isinstance(codec.get("base16g-crc16-rs"), codec.Base16GCodec)
    payload = b"registry compatibility"
    assert base16c.decode(codec.get("base16g-crc16-rs").encode(payload)) == payload


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
    with pytest.raises(ValueError, match=r"unknown codec 'missing'.*base16g-crc16-rs"):
        codec.get("missing")


def test_codec_registry_rejects_duplicate_names():
    existing = dict(codec.Codec._registry)
    try:
        with pytest.raises(ValueError, match="duplicate codec name 'base16g-crc16-rs'"):
            type("DuplicateCodec", (codec.Codec,), {"name": "base16g-crc16-rs", "encode": lambda self, data, **options: [], "decode": lambda self, lines, **options: b""})
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


def test_describe_line_stream_reports_realized_rs_shape():
    """describe_line_stream reports the encoder's realized nsym without decoding."""
    from glyphive.codec.base16c import describe_line_stream

    rng = random.Random(3)
    data = bytes(rng.randrange(256) for _ in range(50 * 1024))
    lines = base16c.encode(data)
    shape = describe_line_stream(lines)
    assert shape.nsym == 27  # verified for 0.12 ratio at 50 KB
    assert shape.nblocks is not None and shape.nblocks > 0
    assert shape.data_lines == sum(1 for l in lines if l.startswith("L"))
    assert shape.parity_lines == sum(1 for l in lines if l.startswith("P"))


def test_describe_line_stream_ambiguous_shape_reports_none():
    """A line-count-inconsistent stream yields nsym=None, never a guess."""
    from glyphive.codec.base16c import describe_line_stream

    data = bytes(range(256))
    lines = base16c.encode(data)
    # Drop all parity lines: data/parity counts no longer match any single nsym.
    only_data = [l for l in lines if not l.startswith("P")]
    shape = describe_line_stream(only_data)
    assert shape.parity_lines == 0
    assert shape.nsym is None


def test_clean_decode_skips_reed_solomon_entirely(monkeypatch):
    """A zero-erasure stream decodes without invoking reedsolo at all (Phase 1)."""
    import reedsolo

    calls = {"n": 0}
    orig = reedsolo.RSCodec.decode

    def spy(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(reedsolo.RSCodec, "decode", spy)

    data = bytes((i * 37) % 256 for i in range(8192))
    lines = base16c.encode(data)
    assert base16c.decode(lines) == data
    assert calls["n"] == 0


def test_damaged_decode_calls_rs_only_for_blocks_with_erasures(monkeypatch):
    """A stream with a few bad lines RS-corrects only the affected blocks (Phase 2)."""
    import reedsolo

    calls = {"n": 0}
    orig = reedsolo.RSCodec.decode

    def spy(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(reedsolo.RSCodec, "decode", spy)

    data = bytes((i * 37) % 256 for i in range(8192))
    lines = base16c.encode(data)
    damaged = list(lines)
    idx = _first_data_line_index(damaged)
    damaged[idx] = _mutate_one_payload_char(damaged[idx])

    assert base16c.decode(damaged) == data
    # One corrupted line is an erasure across the blocks its interleaved bytes
    # land in -- fewer than every block, so clean blocks were skipped.
    nblocks = _encoding_shape(len(data), 60, 0.12)[3]
    assert 0 < calls["n"] < nblocks


def test_crc_false_positive_is_caught_by_the_sha_gate(tmp_path):
    """The clean fast path skips RS; a slipped bad byte still fails loud via SHA.

    Simulate a CRC false positive: a line whose CRC matches but whose payload
    encodes a byte the encoder did not produce. The zero-erasure fast path
    accepts it (no RS), but restore's whole-document SHA-256 gate must reject
    the resulting document -- "no silent corruption" holds without RS.
    """
    import hashlib

    from glyphive import compression, layout
    from glyphive.codec.base16c import _check_chars
    from glyphive.restore import decode as _decode

    raw = bytes((i * 11) % 256 for i in range(2000))
    encoded = base16c.encode(compression.get("none").compress(raw))
    meta = {
        "v": 1, "codec": "base16g-crc16-rs", "comp": "none", "meta": "none",
        "files": 1, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
    }
    pages = layout.paginate(encoded, dict(meta), lines_per_page=30)
    lines = [line for page in pages for line in page.text_lines]

    # Flip one payload char on the LAST data line (past the group header, so it
    # corrupts real payload bytes not the header) and RECOMPUTE its CRC so it
    # "passes" -- a CRC false positive the zero-erasure fast path won't catch.
    data_positions = [i for i, l in enumerate(lines) if l.startswith("L")]
    li = data_positions[-1]
    label, payload, _check = lines[li].split()
    flipped = (ALPHABET[1] if payload[0] == ALPHABET[0] else ALPHABET[0]) + payload[1:]
    lines[li] = f"{label} {flipped} #{_check_chars(label[1:], flipped)}"

    spool = tmp_path / "raw.bin"
    with pytest.raises((_decode.RestoreError, ValueError)) as excinfo:
        with open(spool, "wb") as sink:
            _decode.decode_document_to_spool(lines, sink)
    # The corruption is caught loudly -- either the whole-document SHA gate or a
    # downstream validation, never a silent wrong-bytes success.
    assert "sha256" in str(excinfo.value).lower() or "digest" in str(
        excinfo.value
    ).lower() or "mismatch" in str(excinfo.value).lower()


# --- radix codec family (base8/base32g/base64) -------------------------------

_RADIX_CODECS = [
    "base16-crc16-rs", "base16g-crc16-rs", "base32-crc16-rs", "base32c-crc16-rs",
    "base32g-crc16-rs", "base64-crc16-rs", "base64g-crc16-rs", "base85-crc16-rs",
    "base8g-crc16-rs", "basemaxg-crc16-rs", "z85-crc16-rs",
]


@pytest.mark.parametrize("name", _RADIX_CODECS)
@pytest.mark.parametrize("size", [0, 1, 2, 7, 60, 61, 255, 256, 4000])
def test_radix_family_roundtrips(name, size):
    c = codec.get(name)
    data = bytes(random.Random(size).randrange(256) for _ in range(size))
    lines = c.encode(data, line_width=60, parity_ratio=0.12)
    assert c.decode(lines) == data


def test_denser_codec_uses_fewer_lines():
    """Higher radix packs more bits/char -> fewer lines for the same payload."""
    data = bytes(random.Random(0).randrange(256) for _ in range(4000))
    counts = {
        name: len(codec.get(name).encode(data, line_width=60))
        for name in _RADIX_CODECS
    }
    assert counts["base8g-crc16-rs"] > counts["base16g-crc16-rs"]
    assert counts["base16g-crc16-rs"] > counts["base32g-crc16-rs"]
    assert counts["base32g-crc16-rs"] > counts["base64-crc16-rs"]


def test_base64_is_case_significant_but_base16c_is_not():
    """base64 must NOT case-fold (A=0, a=26 are distinct); base16c may."""
    from glyphive.codec.base16c import BASE16G
    from glyphive.codec.radix import BASE64
    assert BASE16G.case_fold is True
    assert BASE64.case_fold is False
    # A base64 payload round-trips through its own case-preserving path.
    c = codec.get("base64-crc16-rs")
    data = bytes(range(256))
    assert c.decode(c.encode(data)) == data


def test_no_uniform_run_index_per_radix():
    """The index token never prints as a run of identical glyphs, any radix."""
    from glyphive.codec.base16c import _encode_index, _decode_index, BASE16G
    from glyphive.codec.radix import BASE8G, BASE32G, BASE64
    for spec in (BASE8G, BASE16G, BASE32G, BASE64):
        for i in range(0, 5001):
            tok = _encode_index(i, spec)
            assert len(set(tok)) > 1, (spec.name, i, tok)
            assert _decode_index(tok, spec) == i
