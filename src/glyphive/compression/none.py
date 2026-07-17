"""Passthrough compression method."""

from __future__ import annotations

import typing as _ty

from ._base import CompressionMethod, _copy_chunks


class NoneCompression(CompressionMethod):
    name = "none"

    def compress(self, data: bytes, level: _ty.Optional[int] = None) -> bytes:
        return data

    def decompress(self, data: bytes) -> bytes:
        return data

    def compress_stream(self, source, sink, *, level=None, chunk_size=1024 * 1024):
        del level
        _copy_chunks(source, sink, chunk_size=chunk_size)

    def decompress_stream(self, source, sink, *, chunk_size=1024 * 1024):
        _copy_chunks(source, sink, chunk_size=chunk_size)
