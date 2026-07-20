"""Focused tests for OCR-safe layout metadata bootstrapping."""

import hashlib
import io
import os

import pytest

from glyphive import codec, layout


def _document(data=b"protected metadata", *, nsym_line=2):
    meta = {
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    pages = layout.paginate(
        codec.get("base16g-crc16-rs").encode(data, nsym_line=nsym_line),
        meta,
        lines_per_page=13,
    )
    return data, [line for page in pages for line in page.text_lines]


def _mutate_safe_payload(line):
    label, payload, check = line.split()
    replacement = "A" if payload[0] != "A" else "B"
    return f"{label} {replacement + payload[1:]} {check}"


def test_iter_paginate_is_identical_and_checks_declared_count():
    encoded = codec.get("base16g-crc16-rs").encode(b"streamed pagination" * 20)
    base_meta = {
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": 400,
        "sha256": "0" * 64,
    }
    expected = layout.paginate(encoded, dict(base_meta), lines_per_page=13)
    actual = list(
        layout.iter_paginate(iter(encoded), len(encoded), dict(base_meta), lines_per_page=13)
    )
    assert actual == expected

    with pytest.raises(layout.LayoutError, match="more than"):
        list(layout.iter_paginate(iter(encoded), len(encoded) - 1, dict(base_meta), lines_per_page=13))


def test_read_pages_to_spool_matches_compatibility_result():
    data, lines = _document(b"spooled transcript" * 20)
    expected_meta, expected_lines = layout.read_pages(iter(lines))
    spool = io.BytesIO()
    actual_meta, count = layout.read_pages_to_spool(iter(lines), spool)
    spool.seek(0)
    actual_lines = [line.decode().rstrip("\n") for line in spool]
    assert actual_meta == expected_meta
    assert actual_lines == expected_lines
    assert count == len(expected_lines)


def test_parity_pages_zero_is_byte_identical_to_no_parity_pages():
    """K=0 must reproduce the exact pre-parity-pages output (golden check)."""
    encoded = codec.get("base16g-crc16-rs").encode(b"golden regression check" * 10)
    meta = {
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": 240,
        "sha256": "0" * 64,
    }
    default_pages = layout.paginate(encoded, dict(meta), lines_per_page=13)
    explicit_pages = layout.paginate(
        encoded, dict(meta), lines_per_page=13, parity_pages=0
    )
    assert default_pages == explicit_pages

    for page in default_pages:
        for line in page.text_lines:
            first_token = line.split(None, 1)[0]
            assert not first_token.startswith("Q")

    # The human header must round-trip pgpar=0 with no visible token, and the
    # protected machine envelope must round-trip pgpar=0 / page_block_bytes=0.
    header_line = default_pages[0].text_lines[0]
    assert "pgpar=" not in header_line
    header_meta = layout.parse_header(header_line)
    assert "pgpar" not in header_meta

    all_lines = [line for page in default_pages for line in page.text_lines]
    restored_meta, encoded_lines = layout.read_pages(all_lines)
    assert restored_meta["pgpar"] == 0
    assert restored_meta["page_block_bytes"] == 0
    assert restored_meta["pages"] == len(default_pages)
    restored = codec.get("base16g-crc16-rs").decode(encoded_lines)
    assert restored == b"golden regression check" * 10


def test_parity_pages_positive_paginates_to_data_plus_parity_and_round_trips():
    data = b"page parity round trip content" * 30
    encoded = codec.get("base16g-crc16-rs").encode(data)
    meta = {
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    k = 2
    pages = layout.paginate(encoded, dict(meta), lines_per_page=13, parity_pages=k)

    # Data pages come first, parity pages last, by construction.
    d = len(pages) - k
    assert d >= 1
    data_page_slice = pages[:d]
    parity_page_slice = pages[d:]
    assert len(parity_page_slice) == k

    for page in data_page_slice:
        for line in page.encoded_lines:
            assert line.split(None, 1)[0][:1] in ("L", "P")
    for page in parity_page_slice:
        for line in page.encoded_lines:
            assert line.split(None, 1)[0][:1] == "Q"

    # Every page (data and parity) shares the same grand total, continuing
    # page numbers past the data pages.
    for i, page in enumerate(pages, start=1):
        assert page.number == i
        assert page.total == len(pages)

    all_lines = [line for page in pages for line in page.text_lines]
    restored_meta, _count = layout.read_pages(all_lines)
    assert restored_meta["pages"] == d  # machine envelope pages == D, not D+K
    assert restored_meta["pgpar"] == k
    assert restored_meta["page_block_bytes"] > 0


def test_parity_pages_selects_gf216_field_past_255_total_blocks_and_round_trips():
    """Plan 5: data_total + K > 255 must switch to the GF(2^16) page-parity
    field automatically (instead of raising), and restore must round-trip.
    """
    data = os.urandom(90000)
    lines = codec.get("base16g-crc16-rs").encode(data)
    assert len(lines) > 3000  # sanity: enough to clear 250+ data pages below
    meta = {
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    k = 5
    pages = list(
        layout.iter_paginate(
            iter(lines), len(lines), dict(meta), lines_per_page=13, parity_pages=k
        )
    )
    d = len(pages) - k
    assert d + k > 255  # the whole point: this used to be rejected outright

    all_lines = [line for page in pages for line in page.text_lines]
    restored_meta, encoded_lines = layout.read_pages(all_lines)
    assert restored_meta["pgpar"] == k
    assert restored_meta["pgpar_field"] == 16
    assert restored_meta["pages"] == d
    assert encoded_lines == lines
    assert codec.get("base16g-crc16-rs").decode(encoded_lines) == data


def test_parity_pages_still_selects_gf28_field_at_or_under_255_total_blocks():
    data = b"stays on GF(2^8)" * 5
    encoded = codec.get("base16g-crc16-rs").encode(data)
    meta = {
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    pages = layout.paginate(encoded, dict(meta), lines_per_page=13, parity_pages=2)
    all_lines = [line for page in pages for line in page.text_lines]
    restored_meta, _ = layout.read_pages(all_lines)
    assert restored_meta["pgpar_field"] == 8


def test_parity_pages_exceeding_65535_total_blocks_raises():
    meta = {
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": 0,
        "sha256": "0" * 64,
    }
    # The cap check runs before the encoded-line source is ever consumed, so
    # an empty iterator plus a declared (huge) n_encoded is enough to exercise
    # it cheaply -- no need to materialize a million real lines.
    with pytest.raises(layout.LayoutError, match="65535-page Reed-Solomon limit"):
        list(
            layout.iter_paginate(
                iter(()), 1_000_000, dict(meta), lines_per_page=15, parity_pages=5
            )
        )


def test_parity_row_payload_never_wider_than_data_or_safe_cap():
    """Q parity payloads must be <= the widest data payload and <= 60 (F1).

    Regression for a units bug: parity row width was measured from the full
    framed-line length (~73) and passed as the *payload* width, so Q rows
    printed ~72-char payloads -- wider than the 60-char OCR-safe cap, on the
    very redundancy data meant to recover a lost page.
    """
    from glyphive.codec.engine import split_frame

    data = b"parity width invariant payload " * 40
    encoded = codec.get("base16g-crc16-rs").encode(data)
    meta = {
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    pages = layout.paginate(encoded, dict(meta), lines_per_page=14, parity_pages=2)

    def payload_len(line):
        split = split_frame(line)
        return len(split[1]) if split is not None else len(line)

    data_payloads = [
        payload_len(line)
        for page in pages
        for line in page.encoded_lines
        if line[:1] in ("L", "P")
    ]
    q_payloads = [
        payload_len(line)
        for page in pages
        for line in page.encoded_lines
        if line[:1] == "Q"
    ]
    assert q_payloads  # the fixture must actually produce parity lines
    max_data = max(data_payloads)
    assert max(q_payloads) <= max_data
    assert max(q_payloads) <= 60


def test_real_ocr_damage_to_human_metadata_is_display_only():
    data, lines = _document()
    # A badly OCR-mangled compact human header (garbled codec token, junk tail).
    # Restore must ignore it entirely and trust the protected H frames.
    lines[0] = (
        "#!glyphive v1 base16c-crl files=2 bytes=l60 pages=l garbage9:"
    )
    footer_index = next(i for i, line in enumerate(lines) if " PAGE " in line)
    lines[footer_index] = lines[footer_index].replace("PAGE 1/1", "PAGF l/l")

    meta, encoded = layout.read_pages(lines)
    restored = codec.get(meta["codec"]).decode(encoded)

    assert meta["codec"] == "base16g-crc16-rs"
    assert meta["bytes"] == len(data)
    assert restored == data


def test_compact_machine_and_payload_frames_restore_full_transcript():
    # nsym_line=0: an L/P line with all whitespace removed has exactly one
    # token, which is inherently ambiguous for the (separately tested)
    # structural line-parity detection -- see _detect_line_parity_chars's
    # docstring. This test isolates the compact-frame (all-spaces-removed)
    # OCR-robustness path it actually targets.
    data, lines = _document(nsym_line=0)
    compact = []
    for line in lines:
        if line[:1] in {"H", "L", "P"}:
            compact.append(line.replace(" ", ""))
        elif line.startswith("T"):
            machine, _page, _count = line.rsplit(" ", 2)
            compact.append(machine.replace(" ", "") + "PAGE1/1")
        else:
            compact.append(line)

    meta, encoded = layout.read_pages(compact)

    assert codec.get(meta["codec"]).decode(encoded) == data


def test_corrupted_compact_machine_frame_is_rs_recovered():
    _data, lines = _document()
    indexes = [i for i, line in enumerate(lines) if line.startswith("H")]
    for index in indexes[:2]:
        compact = lines[index].replace(" ", "")
        replacement = "A" if compact[6] != "A" else "B"
        lines[index] = compact[:6] + replacement + compact[7:]

    meta, _encoded = layout.read_pages(lines)
    assert meta["codec"] == "base16g-crc16-rs"


def test_machine_header_uses_fixed_width_safe_frames():
    _data, lines = _document()
    header_frames = [line for line in lines if line.startswith("H")]

    assert header_frames
    assert len(header_frames) % 2 == 0
    assert header_frames[::2] == header_frames[1::2]
    assert all(len(line.split()[1]) <= 60 for line in header_frames)
    assert all(len(line) <= 73 for line in header_frames)


def test_one_machine_header_copy_can_be_corrupted_without_guessing():
    _data, lines = _document()
    index = next(i for i, line in enumerate(lines) if line.startswith("H"))
    lines[index] = _mutate_safe_payload(lines[index])

    meta, _encoded = layout.read_pages(lines)
    assert meta["codec"] == "base16g-crc16-rs"


def test_both_copies_of_one_chunk_corrupted_are_rs_recovered():
    _data, lines = _document()
    indexes = [i for i, line in enumerate(lines) if line.startswith("H")]
    lines[indexes[0]] = _mutate_safe_payload(lines[indexes[0]])
    lines[indexes[1]] = _mutate_safe_payload(lines[indexes[1]])

    meta, _encoded = layout.read_pages(lines)
    assert meta["codec"] == "base16g-crc16-rs"


def test_two_distinct_chunks_corrupted_exceed_the_rs_budget():
    _data, lines = _document()
    indexes = [i for i, line in enumerate(lines) if line.startswith("H")]
    # Damage both copies of two *different* chunk indices (0 and 2, i.e. the
    # first two logical H frames) -- more erasures than the single-chunk RS
    # parity can correct, so this must fail loud rather than guess.
    for pair_start in (0, 2):
        for index in indexes[pair_start:pair_start + 2]:
            lines[index] = _mutate_safe_payload(lines[index])

    with pytest.raises(layout.LayoutError, match="frame copies failed"):
        layout.read_pages(lines)


def test_missing_last_machine_header_frame_is_detected_by_envelope_length():
    _data, lines = _document()
    indexes = [i for i, line in enumerate(lines) if line.startswith("H")]
    del lines[indexes[-2]:indexes[-1] + 1]

    with pytest.raises(layout.LayoutError, match="envelope length mismatch"):
        layout.read_pages(lines)


def test_one_missing_machine_header_copy_is_recovered():
    _data, lines = _document()
    index = next(i for i, line in enumerate(lines) if line.startswith("H"))
    del lines[index]

    meta, _encoded = layout.read_pages(lines)
    assert meta["codec"] == "base16g-crc16-rs"


def test_machine_footer_corruption_marks_page_missing_never_uses_page_hint():
    """A damaged T frame marks its page missing, never trusts the PAGE hint.

    T frames carry no duplication or RS (unlike H), so a CRC-damaged footer
    cannot be repaired -- and must never fall back to the unprotected
    ``PAGE n/total`` prose on the same line. The page's identity is left
    unconfirmed (recorded in ``_missing_pages``); read_pages does NOT hard-fail
    here (2026-07-17: a missing page is an erasure the codec's RS may recover),
    it hands the surviving lines to the codec. Its data line survived, so
    decode is attempted rather than raising at the layout layer.
    """
    _data, lines = _document()
    index = next(i for i, line in enumerate(lines) if line.startswith("T"))
    label, payload, check, suffix, count = lines[index].split()
    replacement = "A" if payload[0] != "A" else "B"
    lines[index] = (
        f"{label} {replacement + payload[1:]} {check} {suffix} {count}"
    )

    meta, _encoded = layout.read_pages(lines)
    assert meta["_missing_pages"] == [1]
    assert any("missing page" in w for w in meta["_page_warnings"])


def test_page_footer_verifier_rejects_damaged_protected_footer():
    _data, lines = _document()
    footer_index = next(i for i, line in enumerate(lines) if line.startswith("T"))
    footer = lines[footer_index]
    page_lines = [
        line
        for line in lines[: footer_index + 1]
        if layout._looks_like_encoded(line)
    ]
    assert layout.verify_page_footer(footer, page_lines)
    assert not layout.verify_page_footer(footer[:-1] + "A", [])


def test_unreadable_index_token_is_surfaced_not_silently_dropped():
    """A frame-shaped line with a corrupted label surfaces in _unreadable_lines.

    Real-recovery findings #1/#2: a stray inserted/leading character corrupts a
    line's index token so ``decode_index`` rejects it. Previously the line
    vanished from ``read_pages`` with no signal, only surfacing much later as an
    opaque RS-parameter error. It must now be reported with page + raw text.
    """
    data = b"protected metadata for the unreadable-index test path"
    meta_in = {
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    pages = layout.paginate(
        codec.get("base16g-crc16-rs").encode(data), meta_in, lines_per_page=13
    )
    lines = [line for page in pages for line in page.text_lines]

    from glyphive.codec.engine import _detect_line_parity_chars, split_frame_with_parity

    li = next(i for i, line in enumerate(lines) if line.startswith("L"))
    line_parity_chars = _detect_line_parity_chars(lines, codec.get("base16g-crc16-rs")._spec)
    label, payload, line_parity, check = split_frame_with_parity(
        lines[li], line_parity_chars=line_parity_chars
    )
    # Insert a stray alphabet char into the label so decode_index rejects it.
    corrupted_label = "L" + label[1] + "K" + label[2:]
    parity_field = f" {line_parity}" if line_parity_chars else ""
    lines[li] = f"{corrupted_label} {payload}{parity_field} {check}"

    meta, _encoded = layout.read_pages(lines)
    unreadable = meta["_unreadable_lines"]
    assert len(unreadable) == 1
    assert unreadable[0]["raw"] == lines[li]
    assert unreadable[0]["page"] == 1  # single-page fixture; footer is page 1


def test_conflicting_index_collision_degrades_to_erasure_not_fatal():
    """Two CRC-valid lines claiming one index with different payloads must NOT
    abort the whole decode (plan-1 Fix 3).

    Real-recovery finding #3 (the most dangerous class): a corrupted label that
    decodes to a real-but-wrong index. The old behavior raised a fatal
    ``CodecError``; a single such collision is far better handled by degrading
    that index to an erasure and letting Reed-Solomon rebuild it -- an erasure at
    one index is squarely inside the default parity budget. Only if the resulting
    erasure load exceeds the budget does decode fail, via the budget path.
    """
    from glyphive.codec.engine import (
        CodecError,
        _check_chars,
        _detect_line_parity_chars,
        split_frame_with_parity,
    )

    data = bytes(range(256)) * 4
    c = codec.get("base16g-crc16-rs")
    lines = c.encode(data)

    data_positions = [i for i, line in enumerate(lines) if line.startswith("L")]
    assert len(data_positions) >= 2
    line_parity_chars = _detect_line_parity_chars(lines, c._spec)
    idx0_token = split_frame_with_parity(
        lines[data_positions[0]], line_parity_chars=line_parity_chars
    )[0][1:]
    _label1, payload_from_line1, line_parity1, _check1 = split_frame_with_parity(
        lines[data_positions[1]], line_parity_chars=line_parity_chars
    )
    # The line-parity field (if any) is NOT covered by the check field, so
    # reusing line1's own field verbatim keeps the forged line CRC-valid --
    # it does not need to be internally RS-consistent for this test.
    parity_field = f" {line_parity1}" if line_parity_chars else ""
    forged = (
        f"L{idx0_token} {payload_from_line1}{parity_field} "
        f"#{_check_chars('L', idx0_token, payload_from_line1)}"
    )
    lines[data_positions[1]] = forged

    # No fatal "conflicting duplicate" abort; RS rebuilds the degraded slot.
    assert c.decode(lines) == data


def test_benign_exact_duplicate_lines_do_not_trigger_collision():
    """An exact duplicate line (page OCR'd twice) is not a conflict."""
    data = bytes(range(256)) * 4
    c = codec.get("base16g-crc16-rs")
    lines = c.encode(data)
    doubled = []
    for line in lines:
        doubled.append(line)
        if line[:1] in ("L", "P"):
            doubled.append(line)  # identical duplicate, same index + payload
    assert c.decode(doubled) == data


def test_footer_hash_mismatch_is_advisory_not_a_page_warning():
    """An OCR-style footer-hash mismatch goes to _footer_hash_notes, not warnings.

    A space inserted into a payload (OCR routinely does this) changes the page
    text hash while the L/P line still decodes via CRC/RS. Such a mismatch must
    be recorded as an advisory footer-hash note -- so the CLI can log it quietly
    -- and NOT as a real page-integrity warning, and must not break decode.
    """
    data = b"footer hash advisory note check " * 30
    encoded = codec.get("base16g-crc16-rs").encode(data)
    meta_in = {
        "codec": "base16g-crc16-rs", "comp": "none", "meta": "none",
        "files": 1, "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    pages = layout.paginate(encoded, meta_in, lines_per_page=14)
    lines = [line for page in pages for line in page.text_lines]
    # OCR-style interior space in the first L line's payload (frame still parses).
    for i, line in enumerate(lines):
        if line.startswith("L"):
            parts = line.split()
            parts[1] = parts[1][:5] + " " + parts[1][5:]
            lines[i] = " ".join(parts)
            break

    meta, encoded_lines = layout.read_pages(lines)
    assert len(meta["_footer_hash_notes"]) >= 1
    assert not meta["_page_warnings"]  # advisory, not a real warning
    assert codec.get(meta["codec"]).decode(encoded_lines) == data
