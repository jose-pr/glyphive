"""Focused contracts for bounded archive serialization and parsing."""

import io
import tracemalloc

import pytest

from glyphive import archive


class MeasuringSink(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.largest_write = 0

    def write(self, data):
        self.largest_write = max(self.largest_write, len(data))
        return super().write(data)


class DiscardingSink:
    def write(self, data):
        return len(data)


def test_streaming_archive_large_file_is_chunked_and_deterministic(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    payload = bytes(range(251)) * 200
    (source / "large.bin").write_bytes(payload)

    outputs = []
    for _ in range(2):
        sink = MeasuringSink()
        archive.write_archive(source, sink, use_ignore=False, chunk_size=257)
        assert sink.largest_write <= 257
        outputs.append(sink.getvalue())
    assert outputs[0] == outputs[1] == archive.archive_tree(source, use_ignore=False)

    events = list(archive.iter_record_events(io.BytesIO(outputs[0]), chunk_size=199))
    header = next(event for event in events if isinstance(event, archive.RecordHeader))
    chunks = [event.data for event in events if isinstance(event, archive.RecordChunk)]
    assert header.path == "large.bin"
    assert header.content_length == len(payload)
    assert max(map(len, chunks)) <= 199
    assert b"".join(chunks) == payload


def test_streaming_parser_rejects_limit_truncation_and_trailing_bytes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.bin").write_bytes(b"abcdefgh")
    raw = archive.archive_tree(source, use_ignore=False)

    with pytest.raises(ValueError, match="exceeds limit"):
        list(archive.iter_record_events(io.BytesIO(raw), max_content_bytes=7))
    with pytest.raises(ValueError, match=r"truncated record 0 \(content\)"):
        list(archive.iter_record_events(io.BytesIO(raw[:-1]), chunk_size=3))
    with pytest.raises(ValueError, match="trailing byte"):
        list(archive.iter_record_events(io.BytesIO(raw + b"x")))


def test_archive_writer_peak_allocation_does_not_scale_with_file_size(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = source / "data.bin"

    peaks = []
    for size in (1024 * 1024, 8 * 1024 * 1024):
        with target.open("wb") as stream:
            stream.seek(size - 1)
            stream.write(b"x")
        tracemalloc.start()
        archive.write_archive(
            source, DiscardingSink(), use_ignore=False, chunk_size=64 * 1024
        )
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)

    assert peaks[1] < peaks[0] + 512 * 1024


@pytest.mark.parametrize("chunk_size", [0, -1, True])
def test_archive_streams_reject_bad_chunk_size(tmp_path, chunk_size):
    with pytest.raises(ValueError, match="positive integer"):
        archive.write_archive(tmp_path, io.BytesIO(), chunk_size=chunk_size)
    with pytest.raises(ValueError, match="positive integer"):
        list(archive.iter_record_events(io.BytesIO(), chunk_size=chunk_size))
