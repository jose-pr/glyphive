"""Quarantine restore safety and bounded archive materialization tests."""

import hashlib
import io
import tracemalloc

import pytest

from glyphive import archive, codec, compression, layout
from glyphive.restore import decode, unarchive


def _transcript(raw, *, digest=None, files=1):
    encoded = codec.get("base16c-crc16-rs").encode(compression.get("none").compress(raw))
    meta = {
        "v": 1,
        "codec": "base16c-crc16-rs",
        "comp": "none",
        "meta": "none",
        "files": files,
        "bytes": len(raw),
        "sha256": digest or hashlib.sha256(raw).hexdigest(),
    }
    pages = layout.paginate(encoded, meta, lines_per_page=30)
    return [line for page in pages for line in page.text_lines]


def _raw_tree(tmp_path, content=b"safe"):
    source = tmp_path / "source"
    source.mkdir()
    nested = source / "aa"
    nested.mkdir()
    (nested / "evil").write_bytes(content)
    return archive.archive_tree(source, use_ignore=False)


def test_traversal_is_staged_privately_and_destination_is_unchanged(tmp_path):
    raw = _raw_tree(tmp_path).replace(b"aa/evil", b"../evil")
    destination = tmp_path / "destination"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_bytes(b"untouched")

    with pytest.raises(decode.RestoreError, match="escapes"):
        unarchive.restore_document_spooled(_transcript(raw), destination)
    assert sentinel.read_bytes() == b"untouched"
    assert sorted(path.name for path in destination.iterdir()) == ["keep.txt"]


def test_duplicate_target_is_rejected_before_publication(tmp_path):
    source = tmp_path / "duplicates"
    source.mkdir()
    (source / "x").write_bytes(b"first")
    (source / "y").write_bytes(b"second")
    raw = archive.archive_tree(source, use_ignore=False).replace(b"\x00y", b"\x00x")
    destination = tmp_path / "destination"
    destination.mkdir()

    with pytest.raises(decode.RestoreError, match="duplicate"):
        unarchive.restore_document_spooled(_transcript(raw, files=2), destination)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("failure", ["truncated", "checksum"])
def test_integrity_failure_leaves_destination_unchanged(tmp_path, failure):
    raw = _raw_tree(tmp_path)
    if failure == "truncated":
        raw = raw[:-1]
        lines = _transcript(raw)
        match = "truncated"
    else:
        lines = _transcript(raw, digest="0" * 64)
        match = "sha256 mismatch"
    destination = tmp_path / "destination"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_bytes(b"untouched")

    with pytest.raises((ValueError, decode.RestoreError), match=match):
        unarchive.restore_document_spooled(lines, destination)
    assert sentinel.read_bytes() == b"untouched"
    assert sorted(path.name for path in destination.iterdir()) == ["keep.txt"]


def test_streamed_unarchive_peak_allocation_is_bounded(tmp_path):
    peaks = []
    for size in (64 * 1024, 1024 * 1024):
        case = tmp_path / str(size)
        case.mkdir()
        source = case / "source"
        source.mkdir()
        with (source / "payload.bin").open("wb") as stream:
            stream.seek(size - 1)
            stream.write(b"x")
        raw = archive.archive_tree(source, use_ignore=False)

        tracemalloc.start()
        unarchive.unarchive_spool(
            io.BytesIO(raw), case / "destination", chunk_size=64 * 1024
        )
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
        assert (case / "destination" / "payload.bin").stat().st_size == size

    assert peaks[1] < peaks[0] + 512 * 1024
