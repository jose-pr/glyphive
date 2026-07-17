"""stdlib gzip compression method."""

from __future__ import annotations

import gzip as _gzip
import io
import typing as _ty

from ._base import CompressionMethod, _copy_chunks, _validate_chunk_size


class GzipCompression(CompressionMethod):
    name = "gzip"

    def compress(self, data: bytes, level: _ty.Optional[int] = None) -> bytes:
        source, sink = io.BytesIO(data), io.BytesIO()
        self.compress_stream(source, sink, level=level)
        return sink.getvalue()

    def decompress(self, data: bytes) -> bytes:
        source, sink = io.BytesIO(data), io.BytesIO()
        self.decompress_stream(source, sink)
        return sink.getvalue()

    def compress_stream(self, source, sink, *, level=None, chunk_size=1024 * 1024):
        chunk_size = _validate_chunk_size(chunk_size)
        with _gzip.GzipFile(
            fileobj=sink,
            mode="wb",
            filename="",
            compresslevel=9 if level is None else level,
            mtime=0,
        ) as compressed:
            _copy_chunks(source, compressed, chunk_size=chunk_size)

    def decompress_stream(self, source, sink, *, chunk_size=1024 * 1024):
        chunk_size = _validate_chunk_size(chunk_size)
        with _gzip.GzipFile(fileobj=source, mode="rb") as decompressed:
            _copy_chunks(decompressed, sink, chunk_size=chunk_size)
