"""Optional zstandard compression method with lazy backend imports."""

from __future__ import annotations

import io
import typing as _ty

from ._base import CompressionMethod, _copy_chunks, _validate_chunk_size


def _zstd_module():
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise RuntimeError(
            "zstd compression requires the optional 'zstandard' dependency; "
            "install it with: pip install glyphive[zstd]"
        ) from exc
    return zstandard


def _available_zstd_module():
    """Load zstandard and add the project extra hint to backend failures."""
    try:
        return _zstd_module()
    except RuntimeError as exc:
        message = str(exc)
        if "pip install glyphive[zstd]" not in message:
            message += "; install it with: pip install glyphive[zstd]"
        raise RuntimeError(message) from exc


class ZstdCompression(CompressionMethod):
    name = "zstd"

    @classmethod
    def is_available(cls) -> bool:
        try:
            _zstd_module()
        except RuntimeError:
            return False
        return True

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
        compressor = _available_zstd_module().ZstdCompressor(
            level=3 if level is None else level,
            threads=0,
            write_checksum=True,
            write_content_size=False,
        )
        with compressor.stream_writer(sink, closefd=False) as writer:
            _copy_chunks(source, writer, chunk_size=chunk_size)

    def decompress_stream(self, source, sink, *, chunk_size=1024 * 1024):
        chunk_size = _validate_chunk_size(chunk_size)
        decompressor = _available_zstd_module().ZstdDecompressor()
        with decompressor.stream_reader(source, closefd=False) as reader:
            _copy_chunks(reader, sink, chunk_size=chunk_size)
