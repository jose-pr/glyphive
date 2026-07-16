"""Named printable codecs and the built-in ``g1`` registry entry."""

from __future__ import annotations

import typing as _ty

from ._base import Codec
from .g1 import G1Codec

__all__ = [
    "Codec",
    "G1Codec",
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
