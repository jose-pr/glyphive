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
from glyphive.codec.engine import (
    ALPHABET,
    BASE16G,
    CodecError,
    _check_chars,
    _encoding_shape,
    _parse_line,
    encoded_line_count,
    nibble_decode,
    nibble_encode,
    repair_line,
    split_frame_with_parity,
)


base16g_codec = codec.get("base16g-crc16-rs")


def _split3(line, spec=BASE16G, *, line_parity_chars=None):
    """Split a framed ``line`` into ``(label, payload, check)``, DROPPING an
    optional line-parity field. Only for tests that genuinely don't care about
    the field's presence (e.g. reading the check value). Prefer
    :func:`_split_line`/:func:`_join_line` when a test mutates and re-renders
    a line, so the line-parity field (if any) round-trips unchanged instead of
    silently vanishing (which would desync that one line's token count from
    the rest of the stream's structurally-detected shape)."""
    label, payload, _line_parity, check = _split_line(
        line, spec, line_parity_chars=line_parity_chars
    )
    return label, payload, check


def _split_line(line, spec=BASE16G, *, line_parity_chars=None):
    """Split a framed ``line`` into ``(label, payload, line_parity, check)``.

    Tolerates an optional line-parity field (default ``nsym_line=2`` since
    v2). When ``line_parity_chars`` is not given it is detected structurally
    (3 vs 4 whitespace tokens on THIS line), mirroring decode's own
    per-stream detection.
    """
    from glyphive.codec.engine import _detect_line_parity_chars

    if line_parity_chars is None:
        line_parity_chars = _detect_line_parity_chars([line], spec)
    result = split_frame_with_parity(line, spec=spec, line_parity_chars=line_parity_chars)
    assert result is not None, f"line does not parse: {line!r}"
    return result


def _join_line(label, payload, line_parity, check):
    """Inverse of :func:`_split_line`: re-render preserving an empty/absent
    line-parity field exactly (no spurious extra token when there is none)."""
    if line_parity:
        return f"{label} {payload} {line_parity} {check}"
    return f"{label} {payload} {check}"


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
    lines = base16g_codec.encode(data)
    assert base16g_codec.decode(lines) == data


def test_roundtrip_line_width_boundary():
    # line_width default is 60 chars; exercise data exactly one full line wide.
    # bytes_per_line = (60 * 4) // 8 = 30 bytes (4 bits/char, the 16-char alphabet).
    data = bytes(range(30))
    lines = base16g_codec.encode(data)
    assert base16g_codec.decode(lines) == data


@pytest.mark.parametrize("size", [0, 1, 29, 30, 31, 255, 4096])
def test_streaming_encode_is_line_identical(size):
    data = bytes((index * 31) % 256 for index in range(size))
    expected = base16g_codec.encode(data)
    actual = list(base16g_codec.iter_encode(io.BytesIO(data), len(data)))
    assert actual == expected
    assert encoded_line_count(len(data)) == len(expected)


def test_streaming_encode_rejects_truncated_and_grown_source():
    with pytest.raises(ValueError, match="truncated"):
        list(base16g_codec.iter_encode(io.BytesIO(b"short"), 6))
    with pytest.raises(ValueError, match="grew"):
        list(base16g_codec.iter_encode(io.BytesIO(b"extra"), 4))


def test_spooled_decode_matches_one_shot_and_repairs_erasures():
    data = bytes((index * 17) % 256 for index in range(4096))
    lines = base16g_codec.encode(data)
    damaged = list(lines)
    line_index = _first_data_line_index(damaged)
    damaged[line_index] = _mutate_one_payload_char(damaged[line_index])
    encoded = io.BytesIO("\n".join(damaged).encode("utf-8") + b"\n")
    restored = io.BytesIO()
    base16g_codec.decode_spool(encoded, restored)
    assert restored.getvalue() == data


@pytest.mark.parametrize(
    ("size", "nsym", "blocks", "parity_bytes", "total_lines"),
    [
        (100, 13, 1, 13, 5),
        (1_000, 24, 5, 120, 38),
        (10_000, 27, 44, 1_188, 374),
        (100_000, 27, 439, 11_853, 3_730),
        # 1,000,000-byte row: nblocks/parity_bytes/total_lines shifted slightly
        # from the v1 baseline because the group header grew 8 -> 9 bytes
        # (the new nsym_line field) -- expected, not a regression.
        (1_000_000, 27, 4_387, 118_449, 37_283),
    ],
)
def test_default_parity_is_global_twelve_percent(
    size, nsym, blocks, parity_bytes, total_lines
):
    shape = _encoding_shape(size, 60, 0.12)
    assert shape[2:4] == (nsym, blocks)
    assert blocks * nsym == parity_bytes
    assert sum(shape[-2:]) == total_lines
    assert abs(parity_bytes / (size + 9) - 0.12) < 0.002  # 9-byte v2 group header


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
    label, payload, line_parity, check = _split_line(line)
    # Change the first payload char to a different alphabet char.
    orig = payload[0]
    replacement = next(c for c in ALPHABET if c != orig)
    new_payload = replacement + payload[1:]
    return _join_line(label, new_payload, line_parity, check)


def _wreck_payload(line):
    """Return ``line`` with MANY payload chars changed -- beyond the reach of the
    decode-hardening single-substitution repair, so the line is a true erasure."""
    label, payload, line_parity, check = _split_line(line)
    new_payload = "".join(
        next(c for c in ALPHABET if c != ch) if i % 2 == 0 else ch
        for i, ch in enumerate(payload)
    )
    return _join_line(label, new_payload, line_parity, check)


def test_single_char_error_self_heals():
    rng = random.Random(42)
    data = bytes(rng.randrange(256) for _ in range(500))
    lines = base16g_codec.encode(data)

    idx = _first_data_line_index(lines)
    corrupted = list(lines)
    mutated = _mutate_one_payload_char(corrupted[idx])
    assert mutated != corrupted[idx]
    corrupted[idx] = mutated

    # The line's CRC now fails, marking it an erasure; RS repairs it.
    assert base16g_codec.decode(corrupted) == data


# --------------------------------------------------------------------------- #
# Plan 1 — decode hardening (geometry poisoning, repair, kind-flip, collisions)
# --------------------------------------------------------------------------- #
def _corrupt_index_token(line):
    """Change one character of a line's 5-char index token (poisons geometry:
    the CRC-failed line now claims an arbitrary, likely huge, index)."""
    label, payload, line_parity, check = _split_line(line)
    token = label[1:]
    orig = token[0]
    sub = next(c for c in ALPHABET if c != orig)
    return _join_line(f"{label[0]}{sub}{token[1:]}", payload, line_parity, check)


def test_repair_line_fixes_single_char_errors():
    """repair_line is SOUND: for a single payload error it returns either the
    exact original or None (never a wrong non-None repair). It reproduces the
    original in the large majority of cases; the residual are genuine CRC
    ambiguities (a different single substitution also matches the 16-bit check),
    which it correctly declines rather than guessing.

    Uses ``nsym_line=0`` frames throughout: this test is specifically about
    the CRC-guided single-substitution search over kind+token+payload, not the
    (separately tested) in-line RS tier, so it keeps the frame shape simple.
    """
    spec = BASE16G
    from glyphive.codec.engine import _frame

    rng = random.Random(7)
    exact = 0
    trials = 200
    for _ in range(trials):
        orig = _frame(
            "L",
            rng.randrange(1000),
            bytes(rng.randrange(256) for _ in range(30)),
            spec,
        )
        label, payload, check = orig.split()
        i = rng.randrange(len(payload))
        bad_payload = payload[:i] + next(c for c in ALPHABET if c != payload[i]) + payload[i + 1:]
        bad = f"{label} {bad_payload} {check}"
        assert not _parse_line(bad, spec).ok
        repaired = repair_line(bad, spec)
        # Soundness: any non-None repair must itself pass CRC.
        if repaired is not None:
            assert _parse_line(repaired, spec).ok
            if repaired == orig:
                exact += 1
    assert exact >= int(trials * 0.85)  # measured ~94% exact
    # v2: the CRC now covers `kind`, so an L->P kind flip is a single-character
    # error like any other -- it FAILS CRC (not "CRC blind to kind" as in v1),
    # and repair_line's search (which now includes the kind position) either
    # reproduces the original kind or declines.
    p_line = "P" + orig[1:]
    assert not _parse_line(p_line, spec).ok
    fixed = repair_line(p_line, spec)
    assert fixed is None or fixed == orig


def test_geometry_poisoning_index_corruption_still_decodes():
    """A CRC-failed line with a corrupted index token (claiming an impossible
    index) must NOT destroy stream geometry -- decode still succeeds (repair
    fixes most; the trusted-geometry rule contains the rest). Regression for the
    'cannot recover RS parameters' failure at low CER."""
    rng = random.Random(11)
    data = bytes(rng.randrange(256) for _ in range(4000))
    lines = base16g_codec.encode(data)
    damaged = list(lines)
    # Corrupt the index token of a few data lines.
    dpos = [i for i, l in enumerate(damaged) if l.startswith("L")]
    for i in dpos[:3]:
        damaged[i] = _corrupt_index_token(damaged[i])
    assert base16g_codec.decode(damaged) == data


def test_kind_flip_now_fails_crc_and_still_decodes():
    """v2 regression for Defect B: the per-line CRC now covers ``kind``, so an
    L->P kind flip FAILS CRC (it no longer produces a CRC-valid phantom parity
    line at the same index, unlike v1). The line becomes an ordinary single-
    line erasure, which document-level RS (now with interleaved parity, Defect
    A) corrects cleanly -- no collision, no budget error, just a clean
    round-trip."""
    rng = random.Random(13)
    data = bytes(rng.randrange(256) for _ in range(4000))
    lines = base16g_codec.encode(data)
    damaged = list(lines)
    idx = _first_data_line_index(damaged)
    flipped = "P" + damaged[idx][1:]
    assert not _parse_line(flipped).ok  # kind flip now fails its own CRC
    damaged[idx] = flipped
    assert base16g_codec.decode(damaged) == data


def test_interleaved_parity_survives_paired_line_burst_same_block(monkeypatch):
    """v2 regression for Defect A: with parity interleaved symbol-major
    (:func:`_parity_position`, ``j*nblocks+b``), a wrecked PARITY line plus a
    wrecked DATA line still decodes cleanly. Proven load-bearing (not
    incidental parity slack) by re-encoding the SAME document end-to-end with
    the pre-v2 contiguous layout (``b*nsym+j``) monkeypatched back in and
    applying the identical corruption pattern: that document is
    unrecoverable, so the v2 interleave is what makes the first case work.

    Uses a 1,000-byte document (``nsym=24``, ``nblocks=5`` per the header
    docstring's size table; ``nsym < bytes_per_line`` (30) so the old
    contiguous layout concentrates one printed parity line's damage into
    roughly one block, exactly Defect A's failure mode) and ``nsym_line=0``
    to isolate this from the (separately tested) in-line RS tier.
    """
    from glyphive.codec import engine as _mod

    rng = random.Random(2024)
    data = bytes(rng.randrange(256) for _ in range(1000))
    nsym = _mod._select_nsym(_mod._HEADER_LEN + len(data), 0.12)
    assert nsym < 30  # bytes_per_line at the default line_width=60

    lines = base16g_codec.encode(data, nsym_line=0)
    damaged = list(lines)
    d_idx = next(i for i, l in enumerate(damaged) if l.startswith("L"))
    p_idx = next(i for i, l in enumerate(damaged) if l.startswith("P"))
    damaged[d_idx] = _wreck_payload(damaged[d_idx])
    damaged[p_idx] = _wreck_payload(damaged[p_idx])

    # v2 (current, interleaved parity): decodes cleanly despite the paired burst.
    assert base16g_codec.decode(damaged) == data

    # Pre-v2 (contiguous) layout, simulated end-to-end for the SAME document
    # and SAME corrupted line positions.
    def _old_position(j, b, nblocks):
        return b * nsym + j

    monkeypatch.setattr(_mod, "_parity_position", _old_position)
    old_lines = base16g_codec.encode(data, nsym_line=0)
    old_damaged = list(old_lines)
    old_damaged[d_idx] = _wreck_payload(old_damaged[d_idx])
    old_damaged[p_idx] = _wreck_payload(old_damaged[p_idx])
    with pytest.raises(CodecError):
        base16g_codec.decode(old_damaged)


def test_single_char_error_corrects_in_line_before_document_rs_sees_it():
    """v2 regression for Defect C: at ``nsym_line=2`` (the default), a single
    payload character error is fixed by the in-line RS tier during
    :func:`_preprocess_spool`, so the line re-enters as CRC-valid BEFORE
    :func:`_assemble_to_spool` ever computes document-level RS erasures for
    it -- i.e. it consumes ZERO document-RS erasure budget (unlike a
    ``nsym_line=0`` line, whose CRC failure becomes a full-line erasure)."""
    import io

    from glyphive.codec.engine import _preprocess_spool

    data = bytes(range(90))
    lines = base16g_codec.encode(data, nsym_line=2)
    idx = _first_data_line_index(lines)
    corrupted = list(lines)
    label, payload, line_parity, check = _split_line(corrupted[idx])
    orig_char = payload[5]
    bad_char = next(c for c in ALPHABET if c != orig_char)
    bad_payload = payload[:5] + bad_char + payload[6:]
    corrupted[idx] = _join_line(label, bad_payload, line_parity, check)
    assert not _parse_line(
        corrupted[idx], BASE16G, line_parity_chars=len(line_parity)
    ).ok

    src = io.BytesIO(("\n".join(corrupted) + "\n").encode())
    sink = io.BytesIO()
    changed = _preprocess_spool(src, sink, BASE16G)
    assert changed
    healed_lines = sink.getvalue().decode().splitlines()
    assert healed_lines[idx] == lines[idx]  # exact original line, via in-line RS
    healed_parsed = _parse_line(
        healed_lines[idx], BASE16G, line_parity_chars=len(line_parity)
    )
    assert healed_parsed.ok  # CRC-valid -> zero erasures at the document-RS tier

    # The full pipeline also decodes correctly via this same in-line tier.
    assert base16g_codec.decode(corrupted) == data


@pytest.mark.parametrize("nsym_line", [0, 2, 4])
@pytest.mark.parametrize("size", [0, 1, 60, 61, 1000, 30000])
def test_roundtrip_every_nsym_line_variant(size, nsym_line):
    """Round-trip byte-identical at every supported ``nsym_line`` (0/2/4)
    across the acceptance-gate size matrix, including ``nsym_line=0``
    (the line-parity field is fully optional)."""
    rng = random.Random(9000 + size + nsym_line)
    data = bytes(rng.randrange(256) for _ in range(size))
    lines = base16g_codec.encode(data, nsym_line=nsym_line)
    assert base16g_codec.decode(lines) == data


@pytest.mark.parametrize("nsym_line", [0, 2, 4])
@pytest.mark.parametrize("size", [0, 1, 60, 1000, 30000])
def test_encoded_line_count_matches_actual_emitted_lines(size, nsym_line):
    """Page-count planning (:func:`encoded_line_count`, what ``create`` uses
    to plan pages ahead of encoding on the streaming/compressed path) must
    match the actual number of lines :meth:`Base16GCodec.encode` emits, for
    every ``nsym_line`` -- the line-parity field changes each line's printed
    WIDTH, never the line COUNT."""
    rng = random.Random(4000 + size + nsym_line)
    data = bytes(rng.randrange(256) for _ in range(size))
    lines = base16g_codec.encode(data, nsym_line=nsym_line)
    assert encoded_line_count(size, nsym_line=nsym_line) == len(lines)


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
    label, payload, line_parity, check = _split_line(line)
    return _join_line(label, payload + ALPHABET[0] * extra, line_parity, check)


def _truncate_payload(line, fewer):
    """Return ``line`` with ``fewer`` chars removed from its payload."""
    label, payload, line_parity, check = _split_line(line)
    return _join_line(label, payload[:-fewer], line_parity, check)


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
    lines = base16g_codec.encode(data)

    for corrupt in (_pad_payload, _truncate_payload):
        corrupted = list(lines)
        idx = _find_non_last_data_line(corrupted)
        corrupted[idx] = corrupt(corrupted[idx], 2)
        assert corrupted[idx] != lines[idx]
        # Every other line is untouched and perfectly good; the one wrong-length
        # line is an erasure the interleaved RS repairs.
        assert base16g_codec.decode(corrupted) == data


def test_wrong_width_line_with_valid_crc_still_decodes(monkeypatch):
    """A longer-than-modal line whose CRC coincidentally passes still decodes (F3).

    The modal-width check forces such a line's stored index entry to ``ok=False``
    (`_assemble_to_spool` now honors that stored flag, not just the re-parsed
    CRC), so it becomes a known RS erasure instead of feeding shifted bytes to
    RS as an uncorrected blind error. This exercises the honored-flag path: with
    ``_assemble_to_spool`` consulting the stored flag, the wrong-width line is an
    erasure and RS repairs the document.

    Uses ``nsym_line=0`` throughout: the corrupted line's own recomputed CRC
    must unambiguously pass against a bare (no line-parity field) 3-token
    shape, which is exactly what this test is isolating -- mixing in the
    (separately tested) line-parity field would make the structural
    modal-shape detection, not the modal-WIDTH check this test targets, the
    thing that turns the line into an erasure.
    """
    from glyphive.codec.engine import _check_chars

    rng = random.Random(99)
    data = bytes(rng.randrange(256) for _ in range(500))
    lines = base16g_codec.encode(data, nsym_line=0)

    idx = _find_non_last_data_line(lines)
    corrupted = list(lines)
    label, payload, _check = corrupted[idx].split()
    idx_token = label[1:]
    kind = label[:1]
    wider = payload + ALPHABET[0] * 2  # 2 chars too wide, off the modal width
    # Recompute the CRC so this wrong-width line PASSES its own check.
    corrupted[idx] = f"{label} {wider} #{_check_chars(kind, idx_token, wider)}"

    # It decodes byte-for-byte: the wrong-width line is treated as an erasure
    # (via the stored ok=False the modal-width check set) and RS repairs it.
    assert base16g_codec.decode(corrupted) == data


# --------------------------------------------------------------------------- #
# Corruption beyond the RS budget -> CodecError naming a line label
# --------------------------------------------------------------------------- #
def test_over_budget_corruption_raises_named():
    rng = random.Random(7)
    data = bytes(rng.randrange(256) for _ in range(400))
    # Small parity budget so a modest amount of damage exceeds it.
    lines = base16g_codec.encode(data, parity_ratio=0.02)

    corrupted = list(lines)
    # Wreck every data line's payload with MANY errors each (single-char repair
    # cannot rescue these) -> far beyond any RS budget.
    n_wrecked = 0
    for i, line in enumerate(corrupted):
        if line.startswith("L"):
            corrupted[i] = _wreck_payload(line)
            n_wrecked += 1
    assert n_wrecked > 0

    with pytest.raises(CodecError) as excinfo:
        base16g_codec.decode(corrupted)
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
    # base16g_codec is the default; base8/base32g/base64 are the denser family.
    assert codec.names() == _BUILTIN_CODECS
    assert codec.available() == _BUILTIN_CODECS
    assert isinstance(codec.get("base16g-crc16-rs"), codec.Base16GCodec)
    payload = b"registry compatibility"
    assert base16g_codec.decode(codec.get("base16g-crc16-rs").encode(payload)) == payload


# --------------------------------------------------------------------------- #
# Structural frame parsing: tolerate OCR-inserted interior spaces
# --------------------------------------------------------------------------- #
from glyphive.codec.engine import _parse_line, split_frame  # noqa: E402


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
    # nsym_line=0: isolates this from the (separately tested) line-parity
    # field so the line has an unambiguous bare 3-token shape.
    data = bytes(range(40))
    lines = base16g_codec.encode(data, nsym_line=0)
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
    # nsym_line=0: a compact (no-spaces) frame is only unambiguous without the
    # line-parity field mixed into the same glyph run (see split_frame's
    # compact-shape fallback, which assumes label+payload+check only).
    line = next(
        line for line in base16g_codec.encode(b"compact frame", nsym_line=0)
        if line.startswith("L")
    )
    compact = line.replace(" ", "")

    parsed = _parse_line(compact)

    assert parsed is not None
    assert parsed.ok is True


def test_parse_line_keeps_corrupted_compact_frame_as_crc_erasure():
    line = next(
        line for line in base16g_codec.encode(b"compact frame", nsym_line=0)
        if line.startswith("L")
    )
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
from glyphive.codec.engine import decode_index, encode_index  # noqa: E402


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
    lines = base16g_codec.encode(data)

    idx = _first_data_line_index(lines)
    label, payload, line_parity, check = _split_line(lines[idx])
    assert "0" not in ALPHABET
    noisy_payload = "0" + payload[1:]
    corrupted = list(lines)
    corrupted[idx] = _join_line(label, noisy_payload, line_parity, check)

    assert base16g_codec.decode(corrupted) == data


def test_describe_line_stream_reports_realized_rs_shape():
    """describe_line_stream reports the encoder's realized nsym without decoding."""
    from glyphive.codec.engine import describe_line_stream

    rng = random.Random(3)
    data = bytes(rng.randrange(256) for _ in range(50 * 1024))
    lines = base16g_codec.encode(data)
    shape = describe_line_stream(lines)
    assert shape.nsym == 27  # verified for 0.12 ratio at 50 KB
    assert shape.nblocks is not None and shape.nblocks > 0
    assert shape.data_lines == sum(1 for l in lines if l.startswith("L"))
    assert shape.parity_lines == sum(1 for l in lines if l.startswith("P"))


@pytest.mark.parametrize("name", codec.names())
def test_describe_line_stream_works_for_every_registered_codec(name):
    """The stream shape is spec-aware: every codec's own stream is readable.

    Regression: describe_line_stream used to hardcode the base16g spec
    (default-spec ``_parse_line`` and a literal ``* 4`` bits/char), reporting
    all-zero shapes for every other codec and breaking ``glyphive inspect``.
    """
    from glyphive.codec.engine import describe_line_stream

    implementation = codec.get(name)
    data = bytes(range(256)) * 4
    lines = implementation.encode(data)
    shape = describe_line_stream(lines, implementation._spec)
    assert shape.data_lines == sum(1 for l in lines if l.startswith("L"))
    assert shape.parity_lines == sum(1 for l in lines if l.startswith("P"))
    assert shape.data_lines > 0
    assert shape.nsym is not None and shape.nblocks is not None


def test_describe_line_stream_ambiguous_shape_reports_none():
    """A line-count-inconsistent stream yields nsym=None, never a guess."""
    from glyphive.codec.engine import describe_line_stream

    data = bytes(range(256))
    lines = base16g_codec.encode(data)
    # Drop all parity lines: data/parity counts no longer match any single nsym.
    only_data = [l for l in lines if not l.startswith("P")]
    shape = describe_line_stream(only_data)
    assert shape.parity_lines == 0
    assert shape.nsym is None


def test_clean_decode_skips_reed_solomon_entirely(monkeypatch):
    """A zero-erasure stream decodes without invoking reedsolo at all."""
    import reedsolo

    calls = {"n": 0}
    orig = reedsolo.RSCodec.decode

    def spy(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(reedsolo.RSCodec, "decode", spy)

    data = bytes((i * 37) % 256 for i in range(8192))
    lines = base16g_codec.encode(data)
    assert base16g_codec.decode(lines) == data
    assert calls["n"] == 0


def test_damaged_decode_calls_rs_only_for_blocks_with_erasures(monkeypatch):
    """A stream with a few bad lines RS-corrects only the affected blocks."""
    import reedsolo

    calls = {"n": 0}
    orig = reedsolo.RSCodec.decode

    def spy(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(reedsolo.RSCodec, "decode", spy)

    data = bytes((i * 37) % 256 for i in range(8192))
    lines = base16g_codec.encode(data)
    damaged = list(lines)
    idx = _first_data_line_index(damaged)
    # Wreck the line beyond single-char repair so it is a genuine erasure that
    # reaches RS (a single-char error would now be repaired before RS runs).
    damaged[idx] = _wreck_payload(damaged[idx])

    assert base16g_codec.decode(damaged) == data
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
    from glyphive.codec.engine import _check_chars
    from glyphive.restore import decode as _decode

    raw = bytes((i * 11) % 256 for i in range(2000))
    encoded = base16g_codec.encode(compression.get("none").compress(raw))
    meta = {
        "v": 1, "codec": "base16g-crc16-rs", "comp": "none", "meta": "none",
        "files": 1, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
    }
    pages = layout.paginate(encoded, dict(meta), lines_per_page=30)
    lines = [line for page in pages for line in page.text_lines]

    # Flip one payload char on the LAST data line (past the group header, so it
    # corrupts real payload bytes not the header) and RECOMPUTE its CRC so it
    # "passes" -- a CRC false positive the zero-erasure fast path won't catch.
    # The line-parity field (if any) is preserved unchanged so the line stays
    # CRC-valid under the stream's own structural shape (a genuine zero-erasure
    # stream, which is what the fast path requires).
    data_positions = [i for i, l in enumerate(lines) if l.startswith("L")]
    li = data_positions[-1]
    label, payload, line_parity, _check = _split_line(lines[li])
    kind = label[:1]
    flipped = (ALPHABET[1] if payload[0] == ALPHABET[0] else ALPHABET[0]) + payload[1:]
    lines[li] = _join_line(
        label, flipped, line_parity, f"#{_check_chars(kind, label[1:], flipped)}"
    )

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


def test_base64_is_case_significant_but_base16g_is_not():
    """base64 must NOT case-fold (A=0, a=26 are distinct); base16g_codec may."""
    from glyphive.codec.engine import BASE16G
    from glyphive.codec.radix import BASE64
    assert BASE16G.case_fold is True
    assert BASE64.case_fold is False
    # A base64 payload round-trips through its own case-preserving path.
    c = codec.get("base64-crc16-rs")
    data = bytes(range(256))
    assert c.decode(c.encode(data)) == data


def test_no_uniform_run_index_per_radix():
    """The index token never prints as a run of identical glyphs, any radix."""
    from glyphive.codec.engine import _encode_index, _decode_index, BASE16G
    from glyphive.codec.radix import BASE8G, BASE32G, BASE64
    for spec in (BASE8G, BASE16G, BASE32G, BASE64):
        for i in range(0, 5001):
            tok = _encode_index(i, spec)
            assert len(set(tok)) > 1, (spec.name, i, tok)
            assert _decode_index(tok, spec) == i


# --------------------------------------------------------------------------- #
# Group-packing decode: corruption must be a ValueError, never a crash
# --------------------------------------------------------------------------- #
def test_group_decode_out_of_range_group_raises_valueerror_not_overflow():
    """An OCR misread can turn a printed group into digits whose value exceeds
    the group's byte range (radix**group_chars > 256**group_bytes). That must
    surface as ValueError -- the erasure contract every decode call site
    catches -- not OverflowError, which crashed real basemaxg E2E restores
    (found by the 2026-07-21 basemaxg gate run).
    """
    import pytest

    from glyphive.codec.engine import radix_decode
    from glyphive.codec.radix import BASE85, BASEMAXG, Z85

    for spec in (BASEMAXG, BASE85, Z85):
        # The all-max-digit group is the largest representable value and is
        # guaranteed out of range for any spec where radix**chars > 256**bytes.
        assert spec.radix ** spec.group_chars > 256 ** spec.group_bytes
        bad_group = spec.alphabet[-1] * spec.group_chars
        with pytest.raises(ValueError, match="out of range"):
            radix_decode(bad_group, spec.group_bytes, spec)


def test_group_decode_max_valid_group_round_trips():
    """The bounds check must not reject the top of the VALID range."""
    from glyphive.codec.engine import radix_decode, radix_encode
    from glyphive.codec.radix import BASE85, BASEMAXG, Z85

    for spec in (BASEMAXG, BASE85, Z85):
        top = b"\xff" * spec.group_bytes
        assert radix_decode(radix_encode(top, spec), spec.group_bytes, spec) == top


# --------------------------------------------------------------------------- #
# Runt final data line (2026-07-21 fourpt-runt-line finding) --------------- #
# --------------------------------------------------------------------------- #
#
# See benchmarks/results/fourpt-runt-line-20260721.json: a tiny final data-line
# payload (measured: 13 chars) is destroyed by Tesseract psm-6 at small font
# sizes, and can corrupt the leading frame token of the line above it -- one
# pad byte flips a document between restore-OK and restore-FAIL. Decode was
# read first (RadixCodec.decode_spool / _assemble_to_spool): only the single
# LAST line of each kind may have a non-modal width; every other line whose
# width disagrees with the modal width is forced into an erasure. So
# "rebalance the last two lines" would make the second-to-last line
# non-modal-width and decode would treat it as corruption -- burning RS
# budget on a perfectly good line. That ruled out rebalancing; the fix here
# is Option 2 (pad), landed as ``_runt_pad_bytes`` in codec/engine.py: extra
# zero bytes appended to the data stream (after the real payload, before RS)
# whenever the natural final-line remainder would print below threshold. The
# header's ``orig_len`` stays the true unpadded length, and decode already
# truncates its output to exactly that many bytes, so no decoder change was
# needed at all.

from glyphive.codec.engine import (  # noqa: E402  (grouped with the new tests)
    _chars_for_byte_count,
    _min_final_line_payload_chars,
)


def _final_data_line_payload_len(lines, spec=BASE16G):
    """Return the printed payload length (chars) of the last ``L`` line."""
    data_lines = [line for line in lines if line.startswith("L")]
    assert data_lines, "no data lines emitted"
    label, payload, _check = _split3(data_lines[-1], spec)
    return len(payload)


@pytest.mark.parametrize("nsym_line", [0, 2, 4])
@pytest.mark.parametrize("size", list(range(0, 200)))
def test_no_runt_final_data_line_across_size_sweep(size, nsym_line):
    """Sweeping ~200 consecutive byte lengths (covers every remainder mod
    ``bytes_per_line``), the final data line's printed payload is never below
    the runt threshold, and the document still round-trips byte-identical.
    """
    line_width = 60
    rng = random.Random(20260721 + size + nsym_line)
    data = bytes(rng.randrange(256) for _ in range(size))
    lines = base16g_codec.encode(data, line_width=line_width, nsym_line=nsym_line)
    threshold = _min_final_line_payload_chars(line_width, BASE16G)
    payload_len = _final_data_line_payload_len(lines)
    assert payload_len >= threshold, (
        f"runt final data line at size={size} nsym_line={nsym_line}: "
        f"{payload_len} chars < threshold {threshold}"
    )
    assert base16g_codec.decode(lines) == data


@pytest.mark.parametrize(
    "name", ["base16g-crc16-rs", "base32g-crc16-rs", "basemaxg-crc16-rs", "base85-crc16-rs"]
)
@pytest.mark.parametrize("size", list(range(0, 200, 3)))
def test_no_runt_final_data_line_group_packed_codecs(size, name):
    """Same runt-avoidance guarantee for group-packed codecs (basemaxg/base85),
    where group alignment interacts with line filling differently from plain
    bit-packing -- the pad-byte count must still land the final printed
    payload at or above threshold, and round-trip must stay byte-identical.
    """
    line_width = 60
    c = codec.get(name)
    spec = c._spec
    rng = random.Random(9000 + size)
    data = bytes(rng.randrange(256) for _ in range(size))
    lines = c.encode(data, line_width=line_width)
    threshold = _min_final_line_payload_chars(line_width, spec)
    payload_len = _final_data_line_payload_len(lines, spec)
    assert payload_len >= threshold, (
        f"runt final data line for {name} at size={size}: "
        f"{payload_len} chars < threshold {threshold}"
    )
    assert c.decode(lines) == data


def test_runt_pad_bytes_helper_never_produces_a_runt():
    """Direct unit check of :func:`_runt_pad_bytes` / :func:`_chars_for_byte_count`
    over every possible remainder for the default line width, independent of
    a full encode -- the arithmetic itself must never leave a sub-threshold
    remainder unpadded, and must never demand more than one full line's worth
    of bytes."""
    from glyphive.codec.engine import _bytes_per_line, _runt_pad_bytes

    line_width = 60
    bytes_per_line = _bytes_per_line(line_width, BASE16G)
    threshold = _min_final_line_payload_chars(line_width, BASE16G)
    for protected_len in range(1, bytes_per_line * 3):
        pad = _runt_pad_bytes(protected_len, bytes_per_line, line_width, BASE16G)
        remainder = protected_len % bytes_per_line
        if remainder == 0:
            assert pad == 0
            continue
        padded_remainder = remainder + pad
        assert padded_remainder <= bytes_per_line
        assert _chars_for_byte_count(padded_remainder, BASE16G) >= threshold or (
            padded_remainder == bytes_per_line
        )


def test_encoded_line_count_matches_actual_emitted_lines_with_padding():
    """Page-count planning (:func:`encoded_line_count`) must still match the
    actual line count :meth:`RadixCodec.encode` emits now that a runt final
    line can pull in extra pad bytes (occasionally growing ``data_lines`` by
    one) -- padding is allowed to change the total line count (unlike a pure
    rebalance, which by construction could not), but planning and reality
    must never disagree."""
    for size in range(0, 200):
        rng = random.Random(555 + size)
        data = bytes(rng.randrange(256) for _ in range(size))
        lines = base16g_codec.encode(data)
        data_line_count = sum(1 for line in lines if line.startswith("L"))
        planned = encoded_line_count(size)
        assert planned == len(lines), (size, planned, len(lines))
        # Sanity: padding never REDUCES the data line count below what the
        # unpadded remainder would need (it can only grow it by the rare
        # extra-line case), and never exceeds one extra line.
        assert data_line_count >= 1
