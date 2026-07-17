"""Focused tests for OCR-safe layout metadata bootstrapping."""

import hashlib
import io

import pytest

from glyphive import codec, layout


def _document(data=b"protected metadata"):
    meta = {
        "codec": "g1",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    pages = layout.paginate(
        codec.get("g1").encode(data), meta, lines_per_page=11
    )
    return data, [line for page in pages for line in page.text_lines]


def _mutate_safe_payload(line):
    label, payload, check = line.split()
    replacement = "A" if payload[0] != "A" else "B"
    return f"{label} {replacement + payload[1:]} {check}"


def test_iter_paginate_is_identical_and_checks_declared_count():
    encoded = codec.get("g1").encode(b"streamed pagination" * 20)
    base_meta = {
        "codec": "g1",
        "comp": "none",
        "meta": "none",
        "files": 1,
        "bytes": 400,
        "sha256": "0" * 64,
    }
    expected = layout.paginate(encoded, dict(base_meta), lines_per_page=11)
    actual = list(
        layout.iter_paginate(iter(encoded), len(encoded), dict(base_meta), lines_per_page=11)
    )
    assert actual == expected

    with pytest.raises(layout.LayoutError, match="more than"):
        list(layout.iter_paginate(iter(encoded), len(encoded) - 1, dict(base_meta), lines_per_page=11))


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
        "#!glyphive v=1 codec=gl comp=none meta=none files=2 bytes=160 "
        "pages=1 sha256=cad768eecfe095abd8ceff2c75a5c4df14Ff300b68 9:"
    )
    footer_index = next(i for i, line in enumerate(lines) if " PAGE " in line)
    lines[footer_index] = lines[footer_index].replace("PAGE 1/1", "PAGF l/l")

    meta, encoded = layout.read_pages(lines)
    restored = codec.get(meta["codec"]).decode(encoded)

    assert meta["codec"] == "g1"
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


def test_corrupted_compact_machine_frame_still_fails_crc():
    _data, lines = _document()
    indexes = [i for i, line in enumerate(lines) if line.startswith("H")]
    for index in indexes[:2]:
        compact = lines[index].replace(" ", "")
        replacement = "A" if compact[6] != "A" else "B"
        lines[index] = compact[:6] + replacement + compact[7:]

    with pytest.raises(layout.LayoutError, match="frame copies failed"):
        layout.read_pages(lines)


def test_machine_header_uses_fixed_width_safe_frames():
    _data, lines = _document()
    header_frames = [line for line in lines if line.startswith("H")]

    assert len(header_frames) == 6
    assert header_frames[::2] == header_frames[1::2]
    assert all(len(line.split()[1]) <= 60 for line in header_frames)
    assert all(len(line) <= 73 for line in header_frames)


def test_one_machine_header_copy_can_be_corrupted_without_guessing():
    _data, lines = _document()
    index = next(i for i, line in enumerate(lines) if line.startswith("H"))
    lines[index] = _mutate_safe_payload(lines[index])

    meta, _encoded = layout.read_pages(lines)
    assert meta["codec"] == "g1"


def test_both_machine_header_copies_corrupted_fail_instead_of_guessing():
    _data, lines = _document()
    indexes = [i for i, line in enumerate(lines) if line.startswith("H")]
    lines[indexes[0]] = _mutate_safe_payload(lines[indexes[0]])
    lines[indexes[1]] = _mutate_safe_payload(lines[indexes[1]])

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
    assert meta["codec"] == "g1"


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
    footer = next(line for line in lines if line.startswith("T"))
    page_lines = [line for line in lines if layout._looks_like_encoded(line)]
    assert layout.verify_page_footer(footer, page_lines)
    assert not layout.verify_page_footer(footer[:-1] + "A", [])
