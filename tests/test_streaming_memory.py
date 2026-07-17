"""Fixed-envelope create-path allocation checks (run heavy variants on VM/CI)."""

import tracemalloc

from glyphive import cli


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
