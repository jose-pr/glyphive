"""Named printable codecs and the built-in ``base16g-crc16-rs`` registry entry."""

from __future__ import annotations

import typing as _ty

from ._base import Codec
from .base16c import Base16GCodec
# Importing registers the whole codec family via Codec.__init_subclass__.
# Two families: glyphive-tuned OCR-safe codecs (base16g default, base8, base32g,
# base64) and standard textbook-alphabet codecs (base16, base32, base32c).
# base16g stays the recommended stock-safe default.
from .radix import (
    Base8GCodec,
    Base16Codec,
    Base32Codec,
    Base32CCodec,
    Base32GCodec,
    Base64Codec,
    Base85Codec,
    Z85Codec,
    Base64GCodec,
    BaseMaxGCodec,
)

__all__ = [
    "Codec",
    "Base16GCodec",
    "Base8GCodec",
    "Base16Codec",
    "Base32Codec",
    "Base32CCodec",
    "Base32GCodec",
    "Base64Codec",
    "Base85Codec",
    "Z85Codec",
    "Base64GCodec",
    "BaseMaxGCodec",
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
