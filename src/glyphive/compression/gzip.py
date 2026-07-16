"""stdlib gzip compression method."""

from __future__ import annotations

import gzip as _gzip
import typing as _ty

from ._base import CompressionMethod


class GzipCompression(CompressionMethod):
    name = "gzip"

    def compress(self, data: bytes, level: _ty.Optional[int] = None) -> bytes:
        return _gzip.compress(data, compresslevel=9 if level is None else level)

    def decompress(self, data: bytes) -> bytes:
        return _gzip.decompress(data)
