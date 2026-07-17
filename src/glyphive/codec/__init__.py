"""Named printable codecs and the built-in ``base16c-crc16-rs`` registry entry."""

from __future__ import annotations

import typing as _ty

from ._base import Codec
from .base16c import Base16CCodec

__all__ = [
    "Codec",
    "Base16CCodec",
    "available",
    "get",
    "names",
]


def get(name: str) -> Codec:
    """Return a fresh registered codec implementation by name."""
    return Codec.get(name)


def names() -> _ty.List[str]:
    """Return all registered codec names in stable order."""
    return Codec.names()


def available() -> _ty.List[str]:
    """Return registered codec names currently available to use."""
    return Codec.available()
