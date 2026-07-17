"""Focused tests for OCR-safe layout metadata bootstrapping."""

import hashlib
import io

import pytest

from glyphive import codec, layout


def _document(data=b"protected metadata"):
    meta = {
        "codec": "base16c-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    pages = layout.paginate(
        codec.get("base16c-crc16-rs").encode(data), meta, lines_per_page=13
    )
    return data, [line for page in pages for line in page.text_lines]


def _mutate_safe_payload(line):
    label, payload, check = line.split()
    replacement = "A" if payload[0] != "A" else "B"
    return f"{label} {replacement + payload[1:]} {check}"


def test_iter_paginate_is_identical_and_checks_declared_count():
    encoded = codec.get("base16c-crc16-rs").encode(b"streamed pagination" * 20)
    base_meta = {
        "codec": "base16c-crc16-rs",
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


def test_real_ocr_damage_to_human_metadata_is_display_only():
    data, lines = _document()
    lines[0] = (
        "#!glyphive v=1 codec=base16c-crl comp=none meta=none files=2 bytes=160 "
        "pages=1 sha256=cad768eecfe095abd8ceff2c75a5c4df14Ff300b68 9:"
    )
    footer_index = next(i for i, line in enumerate(lines) if " PAGE " in line)
    lines[footer_index] = lines[footer_index].replace("PAGE 1/1", "PAGF l/l")

    meta, encoded = layout.read_pages(lines)
    restored = codec.get(meta["codec"]).decode(encoded)

    assert meta["codec"] == "base16c-crc16-rs"
    assert meta["bytes"] == len(data)
    assert restored == data


def test_compact_machine_and_payload_frames_restore_full_transcript():
    data, lines = _document()
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
    assert meta["codec"] == "base16c-crc16-rs"


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
    assert meta["codec"] == "base16c-crc16-rs"


def test_both_copies_of_one_chunk_corrupted_are_rs_recovered():
    _data, lines = _document()
    indexes = [i for i, line in enumerate(lines) if line.startswith("H")]
    lines[indexes[0]] = _mutate_safe_payload(lines[indexes[0]])
    lines[indexes[1]] = _mutate_safe_payload(lines[indexes[1]])

    meta, _encoded = layout.read_pages(lines)
    assert meta["codec"] == "base16c-crc16-rs"


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
    assert meta["codec"] == "base16c-crc16-rs"


def test_machine_footer_corruption_fails_instead_of_using_page_hint():
    _data, lines = _document()
    index = next(i for i, line in enumerate(lines) if line.startswith("T"))
    label, payload, check, suffix, count = lines[index].split()
    replacement = "A" if payload[0] != "A" else "B"
    lines[index] = (
        f"{label} {replacement + payload[1:]} {check} {suffix} {count}"
    )

    with pytest.raises(layout.LayoutError, match="footer failed"):
        layout.read_pages(lines)


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
        "codec": "base16c-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    pages = layout.paginate(
        codec.get("base16c-crc16-rs").encode(data), meta_in, lines_per_page=13
    )
    lines = [line for page in pages for line in page.text_lines]

    li = next(i for i, line in enumerate(lines) if line.startswith("L"))
    label, payload, check = lines[li].split()
    # Insert a stray alphabet char into the label so decode_index rejects it.
    corrupted_label = "L" + label[1] + "K" + label[2:]
    lines[li] = f"{corrupted_label} {payload} {check}"

    meta, _encoded = layout.read_pages(lines)
    unreadable = meta["_unreadable_lines"]
    assert len(unreadable) == 1
    assert unreadable[0]["raw"] == lines[li]
    assert unreadable[0]["page"] == 1  # single-page fixture; footer is page 1


def test_conflicting_index_collision_fails_instead_of_silent_overwrite():
    """Two CRC-valid lines claiming one index with different payloads must fail.

    Real-recovery finding #3 (the most dangerous class): a corrupted label that
    decodes to a real-but-wrong index used to silently overwrite a different
    genuine line under blind last-write-wins. Detect the conflict and refuse.
    """
    from glyphive.codec.base16c import CodecError, _check_chars

    data = bytes(range(256)) * 4
    c = codec.get("base16c-crc16-rs")
    lines = c.encode(data)

    data_positions = [i for i, line in enumerate(lines) if line.startswith("L")]
    assert len(data_positions) >= 2
    # Build a genuinely CRC-valid line that claims line 0's index but carries a
    # DIFFERENT payload (borrowed from line 1) -- i.e. finding #3 exactly: a
    # corrupted label decoded to a real-but-wrong index, and the resulting line
    # still passes its own CRC because the check is recomputed for the new label.
    idx0_token = lines[data_positions[0]].split()[0][1:]
    payload_from_line1 = lines[data_positions[1]].split()[1]
    forged = f"L{idx0_token} {payload_from_line1} #{_check_chars(idx0_token, payload_from_line1)}"
    lines[data_positions[1]] = forged

    with pytest.raises(CodecError, match="conflicting duplicate line index"):
        c.decode(lines)


def test_benign_exact_duplicate_lines_do_not_trigger_collision():
    """An exact duplicate line (page OCR'd twice) is not a conflict."""
    data = bytes(range(256)) * 4
    c = codec.get("base16c-crc16-rs")
    lines = c.encode(data)
    doubled = []
    for line in lines:
        doubled.append(line)
        if line[:1] in ("L", "P"):
            doubled.append(line)  # identical duplicate, same index + payload
    assert c.decode(doubled) == data
