"""The radix codec family: base8 / base32g / base64.

Each is the shipped ``base16c`` pipeline (``codec/base16c.py``) with a different
:class:`~glyphive.codec.base16c._RadixSpec` — a wider alphabet packs more bits
per printed character (denser pages), at the cost of stock-OCR reliability.

Density vs. base16c (4 bits/char):
  - ``base8``    : 3 bits/char (0.75x — sparser, most OCR-robust)
  - ``base16c``  : 4 bits/char (the measured stock-safe default)
  - ``base32g``  : 5 bits/char (1.25x — glyphive's measured 32-glyph set)
  - ``base64``   : 6 bits/char (1.5x)

The measured stock-OCR-safe ceiling is 16 characters (A1/size sweeps 2026-07-18);
base32g/base64 are NOT stock-OCR-safe (~14.8% CER stock) but read at 0.0% CER with
a per-font fine-tuned model (see ``.agents/plans/base32_punctuation_ocr_findings.md``).
They are for the trained-model restore path (opt-in per-font OCR model packages).
Codecs are never gated — creation only maps bytes to characters and never needs a
model; choosing a denser codec is the user's informed decision. base16c stays the
recommendation.
"""

from __future__ import annotations

import typing as _ty

from ._base import Codec
from .base16c import BASE16C, Base16CCodec, _RadixSpec

__all__ = [
    "Base8Codec",
    "Base32GCodec",
    "Base64Codec",
    "BASE8",
    "BASE32G",
    "BASE64",
]


# --- base8 -----------------------------------------------------------------
# 8 chars = 3 bits/char. A sparse, maximally-distinct subset of the base16c
# alphabet (all measured stock-safe). check_width = ceil(16/3) = 6; index_width
# = 7 (8**7 = 2,097,152 lines of headroom). index_mask: 7 distinct values < 8.
BASE8: _ty.Final[_RadixSpec] = _RadixSpec(
    name="base8-crc16-rs",
    alphabet="ABCD34XY",
    bits=3,
    check_width=6,
    index_width=7,
    index_mask=(1, 6, 3, 5, 2, 7, 4),
)

# --- base32g ---------------------------------------------------------------
# 32 chars = 5 bits/char = 25% denser than base16c. This is glyphive's own
# measured alphabet ("32g" = 32 glyphive), NOT RFC-4648 base32: it is the
# base16c-16 plus 10 measured-distinct letters/digits plus 6 measured-safe
# punctuation glyphs (? @ ! & + =). It is NOT stock-OCR-safe (stock CER ~14.8%),
# but a per-font fine-tuned model reads it at 0.0% CER clean and blurred
# (2026-07-18 VM measurement; see .agents/plans/base32_punctuation_ocr_findings.md).
# Excludes '#' (frame delimiter) and whitespace. check_width = ceil(16/5) = 4;
# index_width = 4 (32**4 = 1,048,576). index_mask: 4 distinct values < 32.
BASE32G: _ty.Final[_RadixSpec] = _RadixSpec(
    name="base32g-crc16-rs",
    alphabet="ABCDHKLMPRTVXY34EFGNUW2567?@!&+=",
    bits=5,
    check_width=4,
    index_width=4,
    index_mask=(7, 26, 13, 21),
)

# --- base64 ----------------------------------------------------------------
# 64 chars = 6 bits/char (RFC 4648). Densest; NOT stock-OCR-safe. check_width =
# ceil(16/6) = 3 (top 2 bits pad to zero); index_width = 4 (64**4 = 16,777,216).
# index_mask: 4 distinct values < 64.
BASE64: _ty.Final[_RadixSpec] = _RadixSpec(
    name="base64-crc16-rs",
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
    bits=6,
    check_width=3,
    index_width=4,
    index_mask=(11, 46, 23, 58),
)


class Base8Codec(Base16CCodec):
    """Sparse 8-char (3 bits/char) codec — most OCR-robust, least dense."""

    name = "base8-crc16-rs"
    _spec = BASE8


class Base32GCodec(Base16CCodec):
    """base32g: glyphive's 32-char (5 bits/char) codec — 25% denser than base16c.

    Reads at 0.0% CER with a per-font trained model; ~14.8% on stock OCR. Denser
    than base16c but needs the matching trained model for reliable scan restore.
    """

    name = "base32g-crc16-rs"
    _spec = BASE32G


class Base64Codec(Base16CCodec):
    """64-char (6 bits/char) codec — densest; needs a trained model to restore."""

    name = "base64-crc16-rs"
    _spec = BASE64
