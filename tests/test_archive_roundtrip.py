"""Round-trip tests: file tree -> archive stream -> encoded pages -> restore.

Two layers are exercised:

1. ``archive_tree`` -> ``iter_records`` (serialization only).
2. The FULL library pipeline WITHOUT the CLI: archive -> compress -> encode ->
   paginate -> flatten -> decode_document -> unarchive_bytes, compared
   byte-for-byte against the source tree (all three compression methods).
"""

import hashlib
import os

import pytest

from glyphive import archive, codec, compression, layout
from glyphive.render import lines_per_page_for
from glyphive.restore import decode as restore_decode
from glyphive.restore import unarchive as restore_unarchive


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _build_tree(root):
    """Create a mixed tree under ``root`` (a stdlib pathlib.Path).

    Returns a dict describing what was written so tests can assert on it.
    """
    # UTF-8 text file (with a non-ASCII char). Write with newline="" so the
    # on-disk bytes match the literal string (no Windows \n -> \r\n translation).
    text_content = "héllo world\nsecond line\n"
    with (root / "hello.txt").open("w", encoding="utf-8", newline="") as stream:
        stream.write(text_content)

    # Binary file: every byte value, repeated.
    binary = bytes(range(256)) * 10
    (root / "blob.bin").write_bytes(binary)

    # Nested subdir with a file.
    nested = root / "sub" / "deeper"
    nested.mkdir(parents=True)
    note_content = "# nested\n"
    with (nested / "note.md").open("w", encoding="utf-8", newline="") as stream:
        stream.write(note_content)

    # One genuinely-empty directory.
    (root / "emptydir").mkdir()

    return {
        "hello.txt": text_content.encode("utf-8"),
        "blob.bin": binary,
        "sub/deeper/note.md": note_content.encode("utf-8"),
        "emptydir/": None,  # empty dir marker
    }


def _compare_trees(src, dst):
    """Assert ``src`` and ``dst`` (pathlib.Path) hold identical relpaths+bytes.

    Walks both, comparing file bytes and confirming empty dirs are present.
    """
    def snapshot(base):
        files = {}
        dirs = set()
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = os.path.relpath(dirpath, base).replace(os.sep, "/")
            if rel_dir == ".":
                rel_dir = ""
            if rel_dir and not dirnames and not filenames:
                dirs.add(rel_dir)
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, base).replace(os.sep, "/")
                with open(full, "rb") as fh:
                    files[rel] = fh.read()
        return files, dirs

    src_files, src_dirs = snapshot(str(src))
    dst_files, dst_dirs = snapshot(str(dst))
    assert set(src_files) == set(dst_files)
    for rel, content in src_files.items():
        assert dst_files[rel] == content, f"content mismatch for {rel}"
    assert src_dirs == dst_dirs


# --------------------------------------------------------------------------- #
# archive_tree -> iter_records
# --------------------------------------------------------------------------- #
def test_archive_tree_iter_records_roundtrip(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    expected = _build_tree(src)

    stream = archive.archive_tree(src)
    records = list(archive.iter_records(stream))

    got_files = {}
    got_empty_dirs = set()
    for rec in records:
        if rec.type == archive.REC_EMPTY_DIR:
            got_empty_dirs.add(rec.path)
        else:
            got_files[rec.path] = rec.content

    for relpath, content in expected.items():
        if content is None:
            assert relpath.rstrip("/") in got_empty_dirs
        else:
            assert got_files[relpath] == content

    # The empty dir round-tripped and no phantom files appeared.
    assert "emptydir" in got_empty_dirs
    assert set(got_files) == {"hello.txt", "blob.bin", "sub/deeper/note.md"}


# --------------------------------------------------------------------------- #
# Full library pipeline (no CLI), all compression methods
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("comp", ["none", "gzip", "zstd"])
def test_full_library_roundtrip(tmp_path, comp):
    src = tmp_path / "src"
    src.mkdir()
    _build_tree(src)

    # 1) serialize
    raw = archive.archive_tree(src)
    paths = archive.list_paths(src)

    # 2) compress
    payload = compression.get(comp).compress(raw)

    # 3) codec-encode
    encoded = codec.get("base16c-crc16-rs").encode(payload)

    # 4) paginate (build the header meta the layout requires)
    meta = {
        "codec": "base16c-crc16-rs",
        "comp": comp,
        "files": len(paths),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    lpp = lines_per_page_for(11.0)
    pages = layout.paginate(encoded, meta, lines_per_page=lpp)

    # 5) flatten to text lines (as the text renderer would, minus the file I/O)
    text_lines = []
    for page in pages:
        text_lines.extend(page.text_lines)

    # 6) decode the document back to raw archive bytes and unarchive
    dmeta, decoded_raw = restore_decode.decode_document(text_lines)
    assert decoded_raw == raw

    out = tmp_path / "out"
    restore_unarchive.unarchive_bytes(decoded_raw, out)

    _compare_trees(src, out)


def test_restore_rejects_unknown_codec_before_decode(tmp_path):
    raw = archive.archive_tree(tmp_path)
    payload = compression.get("none").compress(raw)
    encoded = codec.get("base16c-crc16-rs").encode(payload)
    meta = {
        "codec": "missing",
        "comp": "none",
        "files": 0,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    pages = layout.paginate(encoded, meta, lines_per_page=11)
    text_lines = [line for page in pages for line in page.text_lines]

    with pytest.raises(ValueError, match=r"unknown codec 'missing'.*base16c-crc16-rs"):
        restore_decode.decode_document(text_lines)


def test_restore_accepts_ocr_confusable_header_and_footer_tokens(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _build_tree(src)

    raw = archive.archive_tree(src, metadata="basic")
    payload = compression.get("none").compress(raw)
    encoded = codec.get("base16c-crc16-rs").encode(payload)
    meta = {
        "codec": "base16c-crc16-rs",
        "comp": "none",
        "meta": "basic",
        "files": len(archive.list_paths(src)),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    pages = layout.paginate(encoded, meta, lines_per_page=11)
    text_lines = [line for page in pages for line in page.text_lines]

    text_lines[0] = text_lines[0].replace("codec=base16c-crc16-rs", "codec=base16c-crl")
    footer_index = next(i for i, line in enumerate(text_lines) if " PAGE " in line)
    text_lines[footer_index] = text_lines[footer_index].replace("1/1", "l/l")

    decoded_meta, decoded_raw = restore_decode.decode_document(text_lines)
    assert decoded_meta["codec"] == "base16c-crc16-rs"
    assert decoded_meta["meta"] == "basic"
    assert decoded_raw == raw


# --------------------------------------------------------------------------- #
# Path-traversal guard
# --------------------------------------------------------------------------- #
def test_path_traversal_record_refused(tmp_path):
    # Synthesize a raw archive stream with a single record whose path escapes
    # the destination via "..", then confirm unarchive_bytes refuses it.
    import struct

    def make_stream(relpath, content):
        path_bytes = relpath.encode("utf-8")
        rec = (
            struct.pack("<BH", archive.REC_FILE, len(path_bytes))
            + path_bytes
            + struct.pack("<IdQ", 0o644, 0.0, len(content))
            + content
        )
        return archive.MAGIC + struct.pack(
            "<BI", archive.V1_FORMAT_VERSION, 1
        ) + rec

    stream = make_stream("../escapee.txt", b"pwned")
    with pytest.raises(restore_unarchive.RestoreError) as excinfo:
        restore_unarchive.unarchive_bytes(stream, tmp_path / "dest")
    assert "escapee.txt" in str(excinfo.value)
    # Nothing was written outside the destination.
    assert not (tmp_path / "escapee.txt").exists()
