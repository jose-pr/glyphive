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


def test_missing_page_is_recovered_by_document_rs_when_budget_allows(tmp_path):
    """A wholly missing page no longer hard-fails; codec RS recovers it.

    User decision 2026-07-17: a missing page is a contiguous erasure burst the
    document-wide interleaved Reed-Solomon can recover outright when the parity
    budget suffices. read_pages records the gap in _missing_pages and lets the
    codec try, instead of raising MissingPageError up front.
    """
    raw = b"whole-page recovery exercise payload " * 40  # multi-page at lpp=12
    c = codec.get("base16c-crc16-rs")
    encoded = c.encode(compression.get("none").compress(raw), parity_ratio=0.35)
    meta = {
        "v": 1, "codec": "base16c-crc16-rs", "comp": "none", "meta": "none",
        "files": 1, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
    }
    pages = layout.paginate(encoded, dict(meta), lines_per_page=14)
    assert len(pages) >= 3

    # Delete every text line belonging to one interior data page.
    victim = pages[1]
    victim_lines = set(victim.text_lines)
    surviving = [
        line
        for page in pages
        for line in page.text_lines
        if line not in victim_lines
    ]

    header_meta, encoded_lines = layout.read_pages(surviving)
    assert 2 in header_meta["_missing_pages"]
    restored = c.decode(encoded_lines)
    assert compression.get("none").decompress(restored) == raw


def _pages_with_parity(raw, *, k, lines_per_page=14, parity_ratio=0.12):
    c = codec.get("base16c-crc16-rs")
    encoded = c.encode(compression.get("none").compress(raw), parity_ratio=parity_ratio)
    meta = {
        "v": 1, "codec": "base16c-crc16-rs", "comp": "none", "meta": "none",
        "files": 1, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
    }
    pages = layout.paginate(
        encoded, dict(meta), lines_per_page=lines_per_page, parity_pages=k
    )
    return c, pages


def _drop_pages(pages, victim_numbers):
    victims = {n for n in victim_numbers}
    victim_lines = {
        line
        for page in pages
        if page.number in victims
        for line in page.text_lines
    }
    return [
        line
        for page in pages
        for line in page.text_lines
        if line not in victim_lines
    ]


def test_page_parity_restores_byte_for_byte_with_interior_page_deleted():
    """K=2, delete 2 interior data pages: restores byte-for-byte via parity."""
    raw = b"page parity interior deletion payload " * 60
    c, pages = _pages_with_parity(raw, k=2)
    data_pages = [p for p in pages if p.number <= len(pages) - 2]
    assert len(data_pages) >= 4  # need real interior pages to delete

    surviving = _drop_pages(pages, [2, 3])

    header_meta, encoded_lines = layout.read_pages(surviving)
    assert header_meta["_reconstructed_pages"] == [2, 3]
    restored = c.decode(encoded_lines)
    assert compression.get("none").decompress(restored) == raw


def test_page_parity_restores_byte_for_byte_with_last_page_deleted():
    """K=2, delete the LAST data page: must not truncate stream shape.

    Phase 0 testing found a missing *last* page breaks RS-parameter recovery
    if its lines aren't re-injected at the correct spool position -- this is
    the explicit regression case for that bug class.
    """
    raw = b"page parity last-page deletion payload " * 60
    c, pages = _pages_with_parity(raw, k=2)
    data_total = len(pages) - 2
    assert data_total >= 2

    surviving = _drop_pages(pages, [data_total])

    header_meta, encoded_lines = layout.read_pages(surviving)
    assert header_meta["_reconstructed_pages"] == [data_total]
    restored = c.decode(encoded_lines)
    assert compression.get("none").decompress(restored) == raw


def test_page_parity_exceeding_k_fails_to_restore():
    """K=2, delete 3 whole data pages (>K): page-parity cannot help.

    ``missing_count (3) > K (2)`` so :func:`read_pages` does not attempt
    page-level reconstruction (``_reconstructed_pages`` stays empty) and falls
    back to Phase-0 behavior: record the gap and let the codec's own
    document-wide Reed-Solomon try. With this much missing, the codec's RS
    correction budget is also exceeded, so decode raises its own named
    ``CodecError`` rather than silently returning wrong bytes -- this is the
    documented fallback, not a raw ``MissingPageError`` from the layout layer
    (that only fires when literally zero data lines survive at all).
    """
    raw = b"page parity budget exceeded payload " * 80
    c, pages = _pages_with_parity(raw, k=2, lines_per_page=14)
    data_total = len(pages) - 2
    assert data_total >= 4  # need 3 non-header data pages to delete

    surviving = _drop_pages(pages, [2, 3, 4])

    header_meta, encoded_lines = layout.read_pages(surviving)
    assert header_meta["_reconstructed_pages"] == []
    assert set(header_meta["_missing_pages"]) == {2, 3, 4}

    from glyphive.codec.base16c import CodecError

    with pytest.raises(CodecError):
        c.decode(encoded_lines)


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


def test_on_progress_reports_staged_then_published_events(tmp_path):
    """A successful restore reports progressive staging then publication.

    Every staged record fires a "staged" event before any "published" event
    fires (publication only starts after every record is staged and
    preflighted), and every "published" event carries a running count/total
    so a caller can render real progress instead of only a final summary.
    """
    source = tmp_path / "source"
    source.mkdir()
    (source / "a").write_bytes(b"alpha")
    (source / "b").write_bytes(b"beta")
    raw = archive.archive_tree(source, use_ignore=False)
    destination = tmp_path / "destination"

    events: list[tuple[str, dict]] = []

    def on_progress(event, **fields):
        events.append((event, fields))

    unarchive.restore_document_spooled(
        _transcript(raw, files=2), destination, on_progress=on_progress
    )

    kinds = [event for event, _fields in events]
    assert kinds.count("staged") == 2
    assert kinds.count("published") == 2
    assert kinds.index("staged") < kinds.index("published")
    last_published = [fields for event, fields in events if event == "published"][-1]
    assert last_published["count"] == last_published["total"] == 2


def test_unreadable_line_is_logged_even_when_decode_subsequently_fails(
    tmp_path, caplog
):
    """The unreadable-index diagnostic surfaces before an RS-budget failure.

    Real-recovery finding #5: at no point did extract report which line broke
    a restore -- every fix required hand-written bisection scripts. Corrupting
    a data line's index token on a tiny document (too little RS budget to
    survive losing that line) must still log which raw line was unreadable and
    on which page, before the eventual CodecError propagates.
    """
    raw = _raw_tree(tmp_path, content=b"unreadable line diagnostic ordering")
    lines = _transcript(raw)
    idx = next(i for i, line in enumerate(lines) if line.startswith("L"))
    label, payload, check = lines[idx].split()
    lines[idx] = f"L{label[1]}K{label[2:]} {payload} {check}"

    with caplog.at_level("WARNING", logger="glyphive.restore"):
        with pytest.raises(Exception):
            unarchive.restore_document_spooled(lines, tmp_path / "destination")

    messages = [r.getMessage() for r in caplog.records]
    assert any("unreadable frame index" in m for m in messages)


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
