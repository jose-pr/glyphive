"""Tests for plan 3 -- OCR-confidence-assisted char-level erasure marking.

Covers, at the codec layer:

- ``_suspect_byte_offsets``: low-confidence PAYLOAD CHARACTER positions ->
  byte offsets, for both bit-packed (base16g) and group-packed (basemaxg)
  specs.
- ``align_payload_char_conf``: raw per-character line confidence -> the
  payload-region slice a CRC-failed line's erasure marking actually uses.
- The two-pass, block-local safety valve: an INCOMPLETE confidence hint
  (marks fewer suspects than are actually wrong) still decodes correctly,
  because a block that fails with the narrow erasure set is retried with
  the touching soft line(s) promoted to a full-span erasure -- today's
  behaviour -- before giving up.
- Gate 2 (the plan's own acceptance criterion): a ~30 KB document at ~1%
  character error where the TRUE wrong positions are supplied as low
  confidence decodes correctly; the identical corruption WITHOUT confidence
  (today's whole-line erasure marking) fails outright.
- Confidence absent (or a plain build): decode is unaffected -- this is
  also exercised implicitly by the full existing suite (regression gate).
- Full pipeline integration through :func:`glyphive.restore.decode.decode_document`
  (OCR confidence threaded through :mod:`glyphive.layout` page assembly).
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile

import pytest

from glyphive import archive, codec, compression, layout
from glyphive.codec.base16c import (
    ALPHABET,
    BASE16G,
    Base16GCodec,
    CodecError,
    _bytes_per_line,
    _num_blocks,
    _select_nsym,
    _suspect_byte_offsets,
    align_payload_char_conf,
)
from glyphive.codec.radix import BASEMAXG
from glyphive.render import lines_per_page_for
from glyphive.restore import decode as restore_decode

base16c = codec.get("base16g-crc16-rs")


# --------------------------------------------------------------------------- #
# _suspect_byte_offsets
# --------------------------------------------------------------------------- #
def test_suspect_byte_offsets_bit_packed_maps_two_chars_per_byte():
    # BASE16G: 4 bits/char -> 2 chars/byte. Chars 0,1 -> byte 0; 2,3 -> byte 1.
    assert _suspect_byte_offsets([0], span=10, spec=BASE16G) == {0}
    assert _suspect_byte_offsets([1], span=10, spec=BASE16G) == {0}
    assert _suspect_byte_offsets([2, 3], span=10, spec=BASE16G) == {1}
    assert _suspect_byte_offsets([0, 5], span=10, spec=BASE16G) == {0, 2}


def test_suspect_byte_offsets_bit_packed_drops_positions_beyond_span():
    # span=1 byte -> only chars 0,1 map inside it; char 4 (byte 2) is dropped.
    assert _suspect_byte_offsets([0, 4], span=1, spec=BASE16G) == {0}


def test_suspect_byte_offsets_group_packed_marks_whole_group():
    # BASEMAXG: group_bytes=6, group_chars=9 -- any suspect char in a group
    # marks all 6 bytes of that group, not just "its own" byte.
    assert BASEMAXG.packing == "group"
    assert BASEMAXG.group_bytes == 6
    assert BASEMAXG.group_chars == 9
    offsets = _suspect_byte_offsets([0], span=12, spec=BASEMAXG)
    assert offsets == {0, 1, 2, 3, 4, 5}
    offsets2 = _suspect_byte_offsets([9], span=12, spec=BASEMAXG)
    assert offsets2 == {6, 7, 8, 9, 10, 11}
    # A char in the second group whose bytes exceed a short span are dropped.
    offsets3 = _suspect_byte_offsets([9], span=8, spec=BASEMAXG)
    assert offsets3 == {6, 7}


# --------------------------------------------------------------------------- #
# align_payload_char_conf
# --------------------------------------------------------------------------- #
def test_align_payload_char_conf_extracts_payload_region():
    line = "LMYCVH ABCD #WXYZ"
    conf = [1.0] * len(line)
    conf[7] = 0.2  # 'A' of the payload
    conf[9] = 0.3  # 'C' of the payload
    payload_conf = align_payload_char_conf(line, conf, BASE16G, line_parity_chars=0)
    assert payload_conf == [0.2, 1.0, 0.3, 1.0]


def test_align_payload_char_conf_handles_line_parity_field():
    line = "LMYCVH ABCD PQ #WXYZ"
    conf = [1.0] * len(line)
    conf[7] = 0.4  # 'A'
    payload_conf = align_payload_char_conf(line, conf, BASE16G, line_parity_chars=2)
    assert payload_conf == [0.4, 1.0, 1.0, 1.0]  # line-parity ("PQ") excluded


def test_align_payload_char_conf_tolerates_interior_ocr_whitespace():
    # An interior space inside the payload run (OCR noise) is stripped the
    # same way split_frame_with_parity strips it.
    line = "LMYCVH AB CD #WXYZ"
    conf = [1.0] * len(line)
    conf[7] = 0.5  # 'A'
    payload_conf = align_payload_char_conf(line, conf, BASE16G, line_parity_chars=0)
    assert payload_conf == [0.5, 1.0, 1.0, 1.0]


def test_align_payload_char_conf_rejects_wrong_length():
    assert align_payload_char_conf("LMYCVH ABCD #WXYZ", [1.0, 1.0], BASE16G) is None


def test_align_payload_char_conf_rejects_non_frame_line():
    line = "PAGE 1/1 sha256=deadbeef"
    assert align_payload_char_conf(line, [1.0] * len(line), BASE16G) is None


# --------------------------------------------------------------------------- #
# Helpers shared by the corruption-injection tests below
# --------------------------------------------------------------------------- #
def _corrupt_chars(line: str, positions) -> str:
    """Substitute the payload characters at ``positions`` (0-based, within
    the payload only) with a different, still-in-alphabet character."""
    parts = line.split(" ")
    label, payload, check = parts[0], parts[1], parts[-1]
    chars = list(payload)
    for pos in positions:
        orig = chars[pos]
        chars[pos] = ALPHABET[(ALPHABET.index(orig) + 1) % len(ALPHABET)]
    return f"{label} {''.join(chars)} {check}"


def _conf_for(line: str, low_positions) -> list:
    """Raw per-character confidence for ``line``: 1.0 everywhere except the
    payload character positions in ``low_positions``, which get 0.1."""
    parts = line.split(" ")
    label = parts[0]
    payload_start = len(label) + 1
    conf = [1.0] * len(line)
    for pos in low_positions:
        conf[payload_start + pos] = 0.1
    return conf


# --------------------------------------------------------------------------- #
# Gate 2: the plan's own acceptance criterion
# --------------------------------------------------------------------------- #
def test_gate2_char_level_marking_succeeds_where_whole_line_marking_fails():
    """~30 KB document, deterministic ~1%-scale corruption (every 6th data
    line, 2 payload chars each -- unfixable by the plan-1 CRC-repair tier,
    which only tries single-character substitutions): supplying the TRUE
    corrupted positions as low confidence decodes successfully and byte-
    identically; the SAME corrupted transcript decoded with today's
    whole-line erasure marking (no confidence) fails outright. Both
    directions asserted in one test, per the plan's acceptance criterion.

    Uses ``_decode_hardened_spool`` directly (bypassing the plan-1 CRC-
    repair pre-pass) so the comparison isolates plan 3's mechanism: a
    2-corrupted-char line can occasionally (~1.5% per line, an accepted,
    documented, PRE-EXISTING risk of ``repair_line``'s CRC-guided single-
    substitution search) be "repaired" to a spuriously-CRC-matching wrong
    value if it went through that tier first -- unrelated to this plan.
    """
    rng_data = bytes((i * 97 + 13) % 256 for i in range(30000))
    lines = base16c.encode(rng_data, line_width=60, parity_ratio=0.12, nsym_line=0)
    num_l = sum(1 for line in lines if line.startswith("L"))
    assert num_l > 900  # sanity: this really is a ~30 KB-scale document

    new_lines = list(lines)
    corrupt_positions = {}
    for i in range(0, num_l, 6):
        new_lines[i] = _corrupt_chars(lines[i], [0, 2])
        corrupt_positions[i] = [0, 2]

    assert len(corrupt_positions) > 150  # enough bad lines to overrun a block

    char_conf = [None] * len(new_lines)
    for i, positions in corrupt_positions.items():
        char_conf[i] = _conf_for(new_lines[i], positions)

    encoded_text = ("\n".join(new_lines) + "\n").encode("utf-8")

    sink = io.BytesIO()
    base16c._decode_hardened_spool(io.BytesIO(encoded_text), sink, char_conf=char_conf)
    assert sink.getvalue() == rng_data  # char-level marking: succeeds, byte-identical

    with pytest.raises(CodecError, match="exceeds RS correction budget"):
        base16c._decode_hardened_spool(io.BytesIO(encoded_text), io.BytesIO())
    # whole-line marking (no confidence, today's behaviour): fails outright


def test_confidence_absent_is_byte_identical_to_no_confidence_build():
    """``char_conf=None`` (the default) must behave exactly like a build
    without this feature at all -- gate 1."""
    data = bytes((i * 31 + 5) % 256 for i in range(5000))
    lines = base16c.encode(data)
    encoded = io.BytesIO(("\n".join(lines) + "\n").encode("utf-8"))
    sink = io.BytesIO()
    base16c.decode_spool(encoded, sink)
    assert sink.getvalue() == data

    encoded2 = io.BytesIO(("\n".join(lines) + "\n").encode("utf-8"))
    sink2 = io.BytesIO()
    base16c.decode_spool(encoded2, sink2, char_conf=None)
    assert sink2.getvalue() == data


# --------------------------------------------------------------------------- #
# Two-pass, block-local safety valve
# --------------------------------------------------------------------------- #
def test_safety_valve_rescues_a_block_when_the_confidence_hint_is_incomplete():
    """Engineered single-block (``nblocks == 1``) scenario, small enough to
    reason about exactly:

    - Two "hard-fail" lines (no confidence at all) each erase their full
      2-byte span -- 4 erasure bytes, matching today's behaviour.
    - One "soft" line has BOTH its bytes wrong, but the confidence hint
      only flags ONE of them (an incomplete/wrong hint) -- so the FIRST
      (char-level) erasure pass leaves 1 byte as an unmarked, genuinely
      wrong ("blind") byte. With nsym=6, ``4 (hard) + 1 (marked) + 2*1
      (blind, RS's error-cost weighting) = 7 > 6``: the first pass
      provably CANNOT succeed (verified directly against reedsolo below).
    - The safety valve promotes the soft line's FULL 2-byte span to
      erasure on retry: ``4 + 2 = 6 <= 6`` -- succeeds, byte-identical.
    """
    import reedsolo

    from glyphive.codec.base16c import (
        _candidate_nsym,
        _detect_line_parity_chars,
        _parity_position,
        _parse_line,
    )

    data = bytes((i * 7 + 3) % 256 for i in range(20))
    lines = base16c.encode(data, line_width=4, parity_ratio=0.2, nsym_line=0)
    new_lines = list(lines)

    # Two always-fully-erased lines (no confidence data at all).
    new_lines[2] = _corrupt_chars(lines[2], [0])
    new_lines[3] = _corrupt_chars(lines[3], [0])

    # The "soft" line: BOTH payload chars corrupted, but only ONE is
    # reported low-confidence -- an incomplete hint.
    new_lines[5] = _corrupt_chars(lines[5], [0, 2])

    char_conf = [None] * len(new_lines)
    char_conf[5] = _conf_for(new_lines[5], [0])  # char 2's corruption is hidden

    encoded_text = ("\n".join(new_lines) + "\n").encode("utf-8")

    # 1) Prove the FIRST pass alone (only the char-level erasures) cannot
    #    succeed -- directly, against reedsolo, bypassing the safety valve.
    codec_obj = Base16GCodec()
    lpc = _detect_line_parity_chars(
        (raw.decode() for raw in io.BytesIO(encoded_text)), BASE16G
    )
    src = io.BytesIO(encoded_text)
    from glyphive.codec.base16c import _assemble_to_spool

    data_lines, parity_lines = {}, {}
    offset_conf = {}
    phys = 0
    src.seek(0)
    while True:
        off = src.tell()
        raw = src.readline()
        if not raw:
            break
        if char_conf[phys] is not None:
            offset_conf[off] = char_conf[phys]
        phys += 1
        parsed = _parse_line(raw.decode().rstrip("\n"), BASE16G, line_parity_chars=lpc)
        if parsed is None:
            continue
        target = data_lines if parsed.kind == "L" else parity_lines
        target[parsed.idx] = (off, parsed.ok, len(parsed.payload))

    data_spool, parity_spool = io.BytesIO(), io.BytesIO()
    data_len, data_erasures, soft_spans = _assemble_to_spool(
        src, data_lines, data_spool, 2, BASE16G, line_conf=offset_conf
    )
    parity_len, parity_erasures, _ = _assemble_to_spool(
        src, parity_lines, parity_spool, 2, BASE16G, line_conf=offset_conf
    )
    assert soft_spans, "the soft (incompletely-marked) line must be tracked"

    nsym = _candidate_nsym(data_len, parity_len)[0]
    nblocks = _num_blocks(data_len, nsym)
    assert nblocks == 1, "test is only meaningful/easy to reason about at nblocks=1"

    data_bytes = data_spool.getvalue()
    parity_bytes = parity_spool.getvalue()
    parity_positions = [_parity_position(j, 0, nblocks) for j in range(nsym)]
    codeword = bytearray(
        data_bytes + bytes(parity_bytes[p] for p in parity_positions)
    )
    rs = reedsolo.RSCodec(nsym)
    with pytest.raises(reedsolo.ReedSolomonError):
        rs.decode(bytearray(codeword), erase_pos=sorted(data_erasures))

    # 2) The FULL decode (with the two-pass safety valve) rescues it.
    sink = io.BytesIO()
    codec_obj._decode_hardened_spool(io.BytesIO(encoded_text), sink, char_conf=char_conf)
    assert sink.getvalue() == data

    # 3) Sanity: a CORRECT (complete) hint succeeds without needing the
    #    valve at all, and the no-confidence baseline (today) also succeeds.
    char_conf_complete = [None] * len(new_lines)
    char_conf_complete[5] = _conf_for(new_lines[5], [0, 2])
    sink2 = io.BytesIO()
    codec_obj._decode_hardened_spool(
        io.BytesIO(encoded_text), sink2, char_conf=char_conf_complete
    )
    assert sink2.getvalue() == data

    sink3 = io.BytesIO()
    codec_obj._decode_hardened_spool(io.BytesIO(encoded_text), sink3)
    assert sink3.getvalue() == data


# --------------------------------------------------------------------------- #
# Full pipeline: OCR confidence threaded through layout page assembly
# --------------------------------------------------------------------------- #
def test_decode_document_uses_char_conf_through_layout():
    """End-to-end: :func:`glyphive.restore.decode.decode_document` threads
    ``char_conf`` through :func:`glyphive.layout.read_pages_to_spool` (which
    re-orders it to match the assembled codec-line spool) down to the
    codec. Builds a real archive -> compress -> encode -> paginate
    transcript (same pipeline as ``test_archive_roundtrip.py``), corrupts a
    deterministic subset of data lines by 2 chars each, and confirms
    confidence-assisted decode recovers the exact original bytes where the
    same corruption without confidence fails.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src")
        os.makedirs(src)
        with open(os.path.join(src, "hello.txt"), "wb") as handle:
            handle.write(b"hello world payload " * 300)

        raw = archive.archive_tree(src)
        paths = archive.list_paths(src)
        payload = compression.get("none").compress(raw)
        encoded = codec.get("base16g-crc16-rs").encode(
            payload, line_width=60, parity_ratio=0.12, nsym_line=0
        )
        meta = {
            "codec": "base16g-crc16-rs",
            "comp": "none",
            "files": len(paths),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        pages = layout.paginate(encoded, meta, lines_per_page=lines_per_page_for(11.0))
        text_lines = []
        for page in pages:
            text_lines.extend(page.text_lines)

        new_lines = list(text_lines)
        corrupt_positions = {}
        count = 0
        for i, line in enumerate(text_lines):
            if line.startswith("L") and " #" in line:
                count += 1
                if count % 4 == 0:
                    new_lines[i] = _corrupt_chars(line, [0, 2])
                    corrupt_positions[i] = [0, 2]
        assert len(corrupt_positions) >= 20

        char_conf = [None] * len(new_lines)
        for i, positions in corrupt_positions.items():
            char_conf[i] = _conf_for(new_lines[i], positions)

        dmeta, decoded_raw = restore_decode.decode_document(new_lines, char_conf=char_conf)
        assert decoded_raw == raw

        with pytest.raises(CodecError):
            restore_decode.decode_document(new_lines)
