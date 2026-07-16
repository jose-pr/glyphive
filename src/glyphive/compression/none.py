"""Passthrough compression method."""

from __future__ import annotations

import typing as _ty

from ._base import CompressionMethod


class NoneCompression(CompressionMethod):
    name = "none"

    def compress(self, data: bytes, level: _ty.Optional[int] = None) -> bytes:
        return data

    def decompress(self, data: bytes) -> bytes:
        return data
