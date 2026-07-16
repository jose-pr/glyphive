"""Named whole-stream compression methods."""

from __future__ import annotations

import typing as _ty

from ._base import CompressionMethod
from .gzip import GzipCompression
from .none import NoneCompression
from .zstd import ZstdCompression

__all__ = [
    "CompressionMethod",
    "GzipCompression",
    "NoneCompression",
    "ZstdCompression",
    "available",
    "default",
    "get",
    "names",
]


def get(name: str) -> CompressionMethod:
    return CompressionMethod.get(name)


def names() -> _ty.List[str]:
    return CompressionMethod.names()


def available() -> _ty.List[str]:
    return CompressionMethod.available()


def default() -> str:
    """Prefer zstd when installed, otherwise use gzip."""
    if ZstdCompression.is_available():
        return "zstd"
    return "gzip"
