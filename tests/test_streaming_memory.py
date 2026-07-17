"""Fixed-envelope create-path allocation checks (run heavy variants on VM/CI)."""

import tracemalloc
import hashlib
import tempfile

from glyphive import archive, cli, codec, layout
from glyphive.restore import decode


def test_text_create_peak_python_allocation_is_bounded(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "payload.bin"
    peaks = []

    for size in (64 * 1024, 256 * 1024):
        with payload.open("wb") as stream:
            stream.seek(size - 1)
            stream.write(b"x")
        output = tmp_path / f"archive-{size}.txt"
        tracemalloc.start()
        assert cli.run(
            [
                "create",
                "-f",
                str(output),
                "-C",
                str(source),
                "--none",
                "--chunk-size",
                str(64 * 1024),
                ".",
            ]
        ) == 0
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)

    assert peaks[1] < peaks[0] + 2 * 1024 * 1024


def test_restore_decode_uses_compact_disk_backed_working_sets(tmp_path):
    peaks = []
    for size in (64 * 1024, 256 * 1024):
        source = tmp_path / f"restore-{size}"
        source.mkdir()
        with (source / "payload.bin").open("wb") as stream:
            stream.seek(size - 1)
            stream.write(b"x")
        raw = archive.archive_tree(source, use_ignore=False)
        encoded = codec.get("g1").encode(raw)
        meta = {
            "v": 1,
            "codec": "g1",
            "comp": "none",
            "meta": "none",
            "files": 1,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        pages = layout.paginate(encoded, meta, lines_per_page=50)
        transcript = [line for page in pages for line in page.text_lines]

        with tempfile.TemporaryFile() as restored:
            tracemalloc.start()
            decode.decode_document_to_spool(iter(transcript), restored)
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peaks.append(peak)
            restored.seek(0, 2)
            assert restored.tell() == len(raw)

    assert peaks[1] < peaks[0] + 8 * 1024 * 1024
