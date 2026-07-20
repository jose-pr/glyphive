"""The radix codec family: base8 / base32g / base64.

Each is the shipped codec engine (``codec/base16c.py``) with a different
:class:`~glyphive.codec.base16c._RadixSpec` — a wider alphabet packs more bits
per printed character (denser pages), at the cost of stock-OCR reliability.

Density vs. base16g (4 bits/char):
  - ``base8``    : 3 bits/char (0.75x — sparser, most OCR-robust)
  - ``base16g``  : 4 bits/char (the measured stock-safe default)
  - ``base32g``  : 5 bits/char (1.25x — glyphive's measured 32-glyph set)
  - ``base64``   : 6 bits/char (1.5x)

The measured stock-OCR-safe ceiling is 16 characters (A1/size sweeps 2026-07-18);
base32g/base64 are NOT stock-OCR-safe (~14.8% CER stock) but read at 0.0% CER with
a per-font fine-tuned model (see ``.agents/plans/base32_punctuation_ocr_findings.md``).
They are for the trained-model restore path (opt-in per-font OCR model packages).
Codecs are never gated — creation only maps bytes to characters and never needs a
model; choosing a denser codec is the user's informed decision. base16g stays the
recommendation.
"""

from __future__ import annotations

import typing as _ty

from ._base import Codec
from .base16c import BASE16G, Base16GCodec, _RadixSpec

__all__ = [
    # glyphive-tuned (OCR-safe) codecs
    "Base8GCodec",
    "Base32GCodec",
    "Base64Codec",
    "BASE8G",
    "BASE32G",
    "BASE64",
    # standard (textbook alphabet) codecs
    "Base16Codec",
    "Base32Codec",
    "Base32CCodec",
    "Base85Codec",
    "Z85Codec",
    "Base64GCodec",
    "BaseMaxGCodec",
    "BASE16",
    "BASE32",
    "BASE32C",
    "BASE85",
    "Z85",
    "BASE64G",
    "BASEMAXG",
]


# --- base8 -----------------------------------------------------------------
# 8 chars = 3 bits/char. base8g = a glyphive-curated maximally-distinct SUBSET of
# the base16g OCR-safe set (NOT standard octal). All 8 measured stock-safe.
# check_width = ceil(16/3) = 6; index_width
# = 7 (8**7 = 2,097,152 lines of headroom). index_mask: 7 distinct values < 8.
BASE8G: _ty.Final[_RadixSpec] = _RadixSpec(
    name="base8g-crc16-rs",
    alphabet="ABCD34XY",
    bits=3,
    check_width=6,
    index_width=7,
    index_mask=(1, 6, 3, 5, 2, 7, 4),
)

# --- base32g ---------------------------------------------------------------
# 32 chars = 5 bits/char = 25% denser than base16g. This is glyphive's own
# measured alphabet ("32g" = 32 glyphive), NOT RFC-4648 base32: it is the
# base16g-16 plus 10 measured-distinct letters/digits plus 6 measured-safe
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

# ---------------------------------------------------------------------------
# Standard (unmodified, textbook) alphabets. These are the plain encodings, NOT
# OCR-tuned: use them when interop with a known base-N alphabet matters, not for
# scan reliability (the OCR-safe choices are base16g / base32g). The '#' frame
# delimiter is not a member of any of these standard sets, so they are safe to
# frame; case handling follows the spec's case_fold (single-case sets fold).
# ---------------------------------------------------------------------------

# --- base16 (hex 0-9 A-F) --------------------------------------------------
# 16 chars = 4 bits/char. Standard hexadecimal; same density as base16g but the
# textbook alphabet (NOT the OCR-safe one). check_width 4; index_width 5.
BASE16: _ty.Final[_RadixSpec] = _RadixSpec(
    name="base16-crc16-rs",
    alphabet="0123456789ABCDEF",
    bits=4,
    check_width=4,
    index_width=5,
    index_mask=(7, 13, 2, 11, 4),
)

# --- base32 (RFC 4648) -----------------------------------------------------
# 32 chars = 5 bits/char. RFC-4648 base32 alphabet (A-Z 2-7). check_width 4;
# index_width 4.
BASE32: _ty.Final[_RadixSpec] = _RadixSpec(
    name="base32-crc16-rs",
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
    bits=5,
    check_width=4,
    index_width=4,
    index_mask=(7, 26, 13, 21),
)

# --- base32c (Crockford base32) --------------------------------------------
# 32 chars = 5 bits/char. Crockford's base32 (0-9 A-Z excluding I L O U).
# check_width 4; index_width 4.
BASE32C: _ty.Final[_RadixSpec] = _RadixSpec(
    name="base32c-crc16-rs",
    alphabet="0123456789ABCDEFGHJKMNPQRSTVWXYZ",
    bits=5,
    check_width=4,
    index_width=4,
    index_mask=(7, 26, 13, 21),
)

# --- base85 / z85 (GROUP packing, non-power-of-2) --------------------------
# 85 chars: not a power of two, so these use Ascii85-style GROUP packing
# (4 bytes -> 5 chars, 0.800 bytes/char -- ~7% denser than base64's 0.750).
# Both canonical alphabets contain '#' (the default frame delimiter), so each
# picks a free delimiter (',' for base85, '\\' for z85 -- both outside their
# alphabets). bits=6 is the floor bits/char, used only for index/check digit
# math (payload uses group packing). check_width=3 (16-bit CRC in base-85
# digits: 85**3 = 614125 > 65535). index_width=5 (85**5 headroom). NOT OCR-safe
# (85 glyphs) -- interop/density only.
BASE85: _ty.Final[_RadixSpec] = _RadixSpec(
    name="base85-crc16-rs",
    alphabet="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
             "!#$%&()*+-;<=>?@^_`{|}~",
    bits=6,
    check_width=3,
    index_width=5,
    index_mask=(7, 13, 2, 11, 4),
    packing="group",
    group_bytes=4,
    group_chars=5,
    delimiter=",",
)

Z85: _ty.Final[_RadixSpec] = _RadixSpec(
    name="z85-crc16-rs",
    alphabet="0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
             ".-:+=^!/*?&<>()[]{}@%$#",
    bits=6,
    check_width=3,
    index_width=5,
    index_mask=(7, 13, 2, 11, 4),
    packing="group",
    group_bytes=4,
    group_chars=5,
    delimiter="\\",
)

# --- base-maxg (glyphive max-distinct set, GROUP packing) ------------------
# 43 chars = the maximal mutually-distinct glyph set measured on stock OCR
# (confusion-graph over ASCII, 2026-07-18; ~43 per font, this is Courier's set).
# radix 43 is non-power-of-2 -> group packing 6 bytes -> 9 chars (0.983 eff,
# ~1.33 bytes/char). check_width 3 (43**3 = 79507 > 65535). index_width 5.
# Like base32g, this is a glyphive-recommended ('g') alphabet that needs a
# trained model for reliable restore (the 43 count is the STOCK-distinct
# ceiling; a per-font model would be trained on exactly this set). '#' is not a
# member, so the default delimiter is fine.
BASEMAXG: _ty.Final[_RadixSpec] = _RadixSpec(
    name="basemaxg-crc16-rs",
    alphabet="!&-012345689;ABCDGIKLMNPRSUVWXY`abdehknrt|~",
    bits=5,
    check_width=3,
    index_width=5,
    index_mask=(7, 13, 2, 11, 4),
    packing="group",
    group_bytes=6,
    group_chars=9,
)


class Base8GCodec(Base16GCodec):
    """Sparse 8-char (3 bits/char) codec — most OCR-robust, least dense."""

    name = "base8g-crc16-rs"
    _spec = BASE8G


class Base32GCodec(Base16GCodec):
    """base32g: glyphive's 32-char (5 bits/char) codec — 25% denser than base16g.

    Reads at 0.0% CER with a per-font trained model; ~14.8% on stock OCR. Denser
    than base16g but needs the matching trained model for reliable scan restore.
    """

    name = "base32g-crc16-rs"
    _spec = BASE32G


class Base64Codec(Base16GCodec):
    """64-char (6 bits/char) codec — densest; needs a trained model to restore."""

    name = "base64-crc16-rs"
    _spec = BASE64


# --- standard (textbook) codecs --------------------------------------------


class Base16Codec(Base16GCodec):
    """Standard hexadecimal (0-9 A-F), 4 bits/char. Not OCR-tuned (use base16g)."""

    name = "base16-crc16-rs"
    _spec = BASE16


class Base32Codec(Base16GCodec):
    """Standard RFC-4648 base32 (A-Z 2-7), 5 bits/char. Not OCR-tuned (use base32g)."""

    name = "base32-crc16-rs"
    _spec = BASE32


class Base32CCodec(Base16GCodec):
    """Crockford base32 (0-9 A-Z minus I L O U), 5 bits/char. Not OCR-tuned."""

    name = "base32c-crc16-rs"
    _spec = BASE32C


class Base85Codec(Base16GCodec):
    """Standard base85 (RFC-1924-ish), group-packed 4->5. Densest; interop only."""

    name = "base85-crc16-rs"
    _spec = BASE85


class Z85Codec(Base16GCodec):
    """ZeroMQ Z85, group-packed 4->5. Interop only (85 glyphs, not OCR-safe)."""

    name = "z85-crc16-rs"
    _spec = Z85


# --- base64g (glyphive curated 64-glyph set) -------------------------------
# 64 chars = 6 bits/char. A glyphive-curated OCR-distinct 64-set. Refined
# 2026-07-18: the initial curated set left a 0.123% trained residual; per-char
# analysis showed it wasn't a few bad glyphs but thin-vertical glyphs (` ~ |)
# plus small-eval jitter. Dropping ` ~ | and RECLAIMING '#' (one of the most
# OCR-distinct glyphs, freed by the per-spec delimiter) + more training drove the
# trained CER to 0.0% clean AND blurred (measured on Nimbus Mono, 400 lines /
# 10000 iters). Because '#' is now a PAYLOAD glyph, base64g uses ',' as its
# check-field delimiter (base16g/etc keep '#'). Case-significant -> case_fold
# False (auto). Needs a trained model for reliable restore (~14.6% stock).
BASE64G: _ty.Final[_RadixSpec] = _RadixSpec(
    name="base64g-crc16-rs",
    alphabet="!\"$%&'(-.0123456789:;>ABCDEGHIJKLMNPRSTUVWXYabcdeghikmnrstyz}#@+",
    bits=6,
    check_width=3,
    index_width=4,
    index_mask=(11, 46, 23, 58),
    delimiter=",",
)


class Base64GCodec(Base16GCodec):
    """base64g: glyphive's curated 64-glyph set (confusion-distinct favoring).

    6 bits/char like base64, but the alphabet favors OCR-distinct glyphs. Needs a
    trained model for reliable restore (as with base32g/base64).
    """

    name = "base64g-crc16-rs"
    _spec = BASE64G


class BaseMaxGCodec(Base16GCodec):
    """base-maxg: glyphive's 43-glyph max-distinct set, group-packed 6->9.

    The largest mutually-distinct alphabet measured on stock OCR (~43/font).
    Needs a trained model for reliable restore, like base32g; denser than 32.
    """

    name = "basemaxg-crc16-rs"
    _spec = BASEMAXG
