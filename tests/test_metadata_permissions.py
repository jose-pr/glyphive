"""Focused tests for archive metadata profiles and restore behavior."""

import hashlib
import os
import struct

import pytest

from glyphive import archive, codec, compression, layout
from glyphive.restore import decode
from glyphive.restore import unarchive


def _v1_stream(path="legacy.txt", content=b"legacy", mode=0o640, mtime=1234.5):
    path_bytes = path.encode("utf-8")
    record = (
        struct.pack("<BH", archive.REC_FILE, len(path_bytes))
        + path_bytes
        + struct.pack("<IdQ", mode, mtime, len(content))
        + content
    )
    return archive.MAGIC + struct.pack(
        "<BI", archive.V1_FORMAT_VERSION, 1
    ) + record


def test_default_none_is_compact_and_zeroes_record_metadata(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_bytes(b"payload")
    (src / "empty").mkdir()

    none = archive.archive_tree(src, use_ignore=False)
    basic = archive.archive_tree(src, use_ignore=False, metadata="basic")

    assert archive.METADATA_PROFILES == ("none", "basic")
    assert archive.stream_metadata(none) == archive.ArchiveMetadata(2, "none")
    assert archive.stream_metadata(basic) == archive.ArchiveMetadata(2, "basic")
    assert len(none) < len(basic)
    assert all((record.mode, record.mtime) == (0, 0.0)
               for record in archive.iter_records(none))


def test_basic_captures_permission_bits_and_millisecond_mtime(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    source = src / "file.txt"
    source.write_bytes(b"payload")
    source.chmod(0o640)
    expected_mtime = 1_700_000_000.123
    os.utime(source, (expected_mtime, expected_mtime))

    raw = archive.archive_tree(src, use_ignore=False, metadata="basic")
    record = next(archive.iter_records(raw))
    assert record.mode == (source.stat().st_mode & 0o7777)
    assert record.mtime == pytest.approx(round(expected_mtime, 3), abs=0.001)

    dest = tmp_path / "dest"
    unarchive.unarchive_bytes(raw, dest)
    restored = dest / "file.txt"
    assert restored.exists()
    assert restored.stat().st_mtime == pytest.approx(record.mtime, abs=0.01)
    if os.name != "nt":
        assert restored.stat().st_mode & 0o7777 == record.mode


def test_v1_fixture_remains_readable_and_restores_metadata(tmp_path):
    raw = _v1_stream()
    assert archive.stream_metadata(raw) == archive.ArchiveMetadata(1, "basic")
    record = next(archive.iter_records(raw))
    assert record.mode == 0o640
    assert record.mtime == pytest.approx(1234.5)

    dest = tmp_path / "dest"
    unarchive.unarchive_bytes(raw, dest)
    assert (dest / "legacy.txt").read_bytes() == b"legacy"
    assert (dest / "legacy.txt").stat().st_mtime == pytest.approx(1234.5, abs=0.01)
    if os.name != "nt":
        assert (dest / "legacy.txt").stat().st_mode & 0o7777 == 0o640


@pytest.mark.parametrize(
    "raw, pattern",
    [
        (archive.MAGIC + struct.pack("<BBI", 2, 2, 0), "unknown archive metadata flags"),
        (archive.MAGIC + struct.pack("<BI", 9, 0), "unsupported archive format version"),
    ],
)
def test_unknown_stream_header_is_rejected(raw, pattern):
    with pytest.raises(ValueError, match=pattern):
        archive.stream_metadata(raw)


def test_truncated_and_trailing_streams_are_rejected(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_bytes(b"payload")
    raw = archive.archive_tree(src, use_ignore=False, metadata="basic")

    with pytest.raises(ValueError, match="truncated record"):
        list(archive.iter_records(raw[:-1]))
    with pytest.raises(ValueError, match="trailing byte"):
        list(archive.iter_records(raw + b"x"))


def test_none_restore_does_not_overwrite_existing_metadata(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_bytes(b"payload")
    raw = archive.archive_tree(src, use_ignore=False, metadata="none")

    dest = tmp_path / "dest"
    dest.mkdir()
    existing = dest / "file.txt"
    existing.write_bytes(b"payload")
    existing.chmod(0o600)
    before = existing.stat()
    os.utime(existing, (1_600_000_000, 1_600_000_000))
    before = existing.stat()

    unarchive.unarchive_bytes(raw, dest)
    after = existing.stat()
    assert after.st_mode & 0o7777 == before.st_mode & 0o7777
    assert after.st_mtime == pytest.approx(before.st_mtime, abs=0.01)


def test_decode_resolves_profile_and_checks_header_profile(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_bytes(b"payload")
    raw = archive.archive_tree(src, use_ignore=False, metadata="none")
    encoded = codec.get("g1").encode(compression.get("none").compress(raw))
    meta = {
        "v": 1,
        "codec": "g1",
        "comp": "none",
        "files": 1,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    pages = layout.paginate(encoded, meta, lines_per_page=11)
    lines = [line for page in pages for line in page.text_lines]

    resolved, decoded = decode.decode_document(lines)
    assert decoded == raw
    assert resolved["meta"] == "none"
    assert resolved["archive_version"] == 2

    header_index = next(i for i, line in enumerate(lines) if line.startswith("#!glyphive"))
    lines[header_index] += " meta=basic"
    with pytest.raises(unarchive.RestoreError, match="profile mismatch"):
        decode.decode_document(lines)
