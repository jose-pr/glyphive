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


def _existing_destination_tree(tmp_path, files):
    """A pre-existing destination directory with the given {relpath: bytes}."""
    destination = tmp_path / "destination"
    destination.mkdir()
    for relpath, content in files.items():
        target = destination / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return destination


def test_overwrite_publication_failure_rolls_back_replaced_files(tmp_path, monkeypatch):
    """A publish failure partway through overwrite restores every prior file.

    Two files are overwritten; the second staged->final move is made to raise.
    The first file (already replaced with new content) must be rolled back to
    its original bytes, not left half-migrated, and the untouched second file
    must keep its original content too (the whole publish is undone).
    """
    import os as _os

    from pathlib_next import Path as _Path

    source = tmp_path / "source"
    source.mkdir()
    (source / "a").write_bytes(b"new-a")
    (source / "b").write_bytes(b"new-b")
    raw = archive.archive_tree(source, use_ignore=False)
    destination = _existing_destination_tree(
        tmp_path, {"a": b"old-a", "b": b"old-b"}
    )

    concrete_path = type(_Path("."))
    real_replace = concrete_path.replace
    calls = {"n": 0}

    def flaky_replace(self, target):
        # Let the first staged->final move (file "a") through, then fail on
        # the second real content move so rollback has something to undo.
        if ".glyphive-rollback" not in str(self) and str(self).endswith(
            _os.sep + "b"
        ):
            calls["n"] += 1
            raise OSError("simulated disk failure during publication")
        return real_replace(self, target)

    monkeypatch.setattr(concrete_path, "replace", flaky_replace)

    with pytest.raises(OSError, match="simulated disk failure"):
        unarchive.restore_document_spooled(
            _transcript(raw, files=2), destination, overwrite=True
        )

    assert calls["n"] == 1
    assert (destination / "a").read_bytes() == b"old-a"
    assert (destination / "b").read_bytes() == b"old-b"


def test_overwrite_publication_failure_removes_newly_created_files(tmp_path, monkeypatch):
    """A publish failure rolls back a newly created (not pre-existing) file too.

    File "a" pre-exists and is overwritten successfully; file "b" is new (no
    prior final target) and its move is made to fail. On rollback "a" must be
    restored to its original content and "b" must not exist at all (it had no
    backup to restore -- the created file itself is removed).
    """
    import os as _os

    from pathlib_next import Path as _Path

    source = tmp_path / "source"
    source.mkdir()
    (source / "a").write_bytes(b"new-a")
    (source / "b").write_bytes(b"new-b")
    raw = archive.archive_tree(source, use_ignore=False)
    destination = _existing_destination_tree(tmp_path, {"a": b"old-a"})

    concrete_path = type(_Path("."))
    real_replace = concrete_path.replace

    def flaky_replace(self, target):
        if ".glyphive-rollback" not in str(self) and str(self).endswith(
            _os.sep + "b"
        ):
            raise OSError("simulated disk failure during publication")
        return real_replace(self, target)

    monkeypatch.setattr(concrete_path, "replace", flaky_replace)

    with pytest.raises(OSError, match="simulated disk failure"):
        unarchive.restore_document_spooled(
            _transcript(raw, files=2), destination, overwrite=True
        )

    assert (destination / "a").read_bytes() == b"old-a"
    assert not (destination / "b").exists()


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
