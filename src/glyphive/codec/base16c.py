"""glyphive codec ``base16g-crc16-rs`` — byte stream ↔ OCR-safe printable text lines.

This module is the core of the format. The alphabet is the **measured-safe**
16-character set below, every line carries its own CRC check, and Reed-Solomon
parity lets small OCR errors be corrected rather than silently propagating. It
is pure: bytes in, list-of-str lines out (and back). No file I/O, no
``pathlib_next``, no ``argparse`` here.

The alphabet
------------
``ALPHABET = "ABCDHKLMPRTVXY34"`` (16 characters -> 4 bits/character). This is
not hand-picked: it is the exact set measured safe on Courier 8pt @ 300 DPI /
Tesseract 5.4.0 (``tools/ocr_font_report.py``) — **16/16 characters read back
with 0% error, 0% line-insertion rate, and zero corrupting confusions** over
~6,600 sampled characters. The previous 32-character Crockford-Base32 alphabet
is not safe on this channel: Crockford *keeps* ``Q`` and ``J`` while excluding
``O``/``I``/``L``/``U``, but ``Q`` is misread as ``O`` (which then alias-maps to
``0``) and ``J`` is misread as ``I`` (which then alias-maps to ``1``) — both are
*silent, undetectable data corruption*, not a recoverable erasure. Trading 5
bits/char for 4 costs 25% more pages; that is the price of a format that
actually round-trips. See the public wire-format and OCR guides for the
derivation and multi-radix measurement method.

Frame grammar (one printed line)
--------------------------------
Every printed line has exactly this shape (single ASCII spaces as separators)::

    <kind><idx> <payload> [<line-parity>] #<check>

- ``<kind>``   : ``L`` for a data line, ``P`` for a Reed-Solomon parity line.
- ``<idx>``    : the line's 0-based index within its stream (data lines and
                 parity lines are indexed independently, each starting at 0),
                 rendered as ``INDEX_WIDTH`` (5) alphabet characters with a
                 fixed per-position XOR mask applied before rendering (see
                 ``encode_index``) so the token never prints as a run of
                 identical glyphs.
- ``<payload>``: exactly ``line_width`` alphabet characters, EXCEPT the last
                 data line, which may be shorter (it carries the remainder).
                 Parity lines are always exactly ``line_width`` wide.
- ``<line-parity>``: OPTIONAL, present iff the stream's ``nsym_line`` (recorded
                 in the group header) is non-zero. ``nsym_line`` Reed-Solomon
                 parity bytes computed over ``idx_token.encode() + <the raw
                 bytes the payload encodes>``, rendered in the line's own
                 alphabet (``ceil(nsym_line * 8 / spec.bits)`` characters). On
                 CRC failure, decode blind-corrects this token+payload
                 codeword using these bytes, re-renders it, and accepts the
                 fix only if the RE-COMPUTED check field then matches the
                 originally printed one (see "Per-line Reed-Solomon" below).
                 NOT covered by the check field.
- ``#``        : a literal ``#`` marking the start of the check field.
- ``<check>``  : ``CHECK_WIDTH`` (4) alphabet characters (16 bits) encoding a
                 16-bit CRC-16/CCITT (poly 0x1021, init 0xFFFF) computed over
                 the bytes ``kind.encode() + idx_token.encode() +
                 payload.encode()`` — i.e. over the *printed* kind letter,
                 index token, and payload characters (the optional
                 line-parity field is deliberately excluded), so a human can
                 recompute it from the page by hand. Covering ``kind`` means
                 an OCR misread that flips ``L``<->``P`` (or ``H``/``T``/``Q``
                 in ``layout.py``) now fails the check instead of silently
                 producing a CRC-valid phantom line at the same index.

Why 4 check characters (not 2), and why 4 is exact now
-------------------------------------------------------
A CRC-16 is a 16-bit value (0..65535). At 4 bits/character (this alphabet),
2 characters would hold only 8 bits (256 values) — far too few. 4 characters
hold exactly 16 bits: ``CHECK_WIDTH(4) * 4 bits/char = 16 bits``, which is the
*exact* width of a CRC-16 with zero waste and, critically, no truncation. (The
prior 5-bit/char alphabet needed 4 characters too, but only used 16 of the 20
bits it spent — this alphabet spends exactly what it needs.) This detects any
single-character OCR substitution in a line and localizes it to exactly that
one line without decoding anything downstream; the embedded index token catches
OCR line-merge / line-drop.

Header layout of the encoded stream
-----------------------------------
The very first bytes of a group's payload carry a 9-byte binary header so decode
can reconstruct the exact original byte length (nibble bit-packing pads the
final 4-bit group, so the raw length must be carried, never guessed):

    b"B1" | version:u8 | nsym:u8 | nsym_line:u8 | orig_len:u32-big-endian

``nsym_line`` (0, 2, or 4) is the per-line Reed-Solomon parity byte count
requested at encode time (see "Per-line Reed-Solomon" below); decode needs it
before it can even find the payload/check boundary of a line, so it is also
detected STRUCTURALLY from the printed line shape (4 whitespace-delimited
tokens vs 3) and cross-checked against this header field once the header
itself is recovered -- a mismatch is a hard decode error, never silently
resolved either way. The header bytes are part of the RS-protected data
stream, so they are covered by parity and per-line CRC just like the payload.

Reed-Solomon parity
-------------------
Parity is computed over the group's data bytes (header + original bytes) with
``reedsolo.RSCodec(nsym)``. The data is split into interleaved RS blocks of at
most 255 bytes so arbitrarily large documents are supported while each block
stays within the GF(2^8) code length. Glyphive chooses one ``nsym`` in 2..100
whose aggregate parity bytes across all blocks are closest to
``protected_bytes * parity_ratio``. Because the per-line CRC tells decode
*exactly which* lines are wrong, decode feeds those lines' byte positions to RS
as **erasures** — RS corrects up to ``nsym`` erasures per block (twice its
blind-error budget).

The document parity BYTE STREAM itself is interleaved symbol-major: parity
byte ``j`` of block ``b`` sits at printed-stream offset ``j * nblocks + b``
(not ``b * nsym + j``). A single bad parity LINE therefore only ever costs
each block at most ``ceil(line_width_bytes / nblocks)`` parity symbols instead
of erasing one block's entire parity budget outright -- the same
burst-spreading logic the data stream already gets from its own
interleaving, just applied to the parity stream too. Pure permutation, zero
size cost.

Per-line Reed-Solomon (``nsym_line``, default 2)
-------------------------------------------------
A single misread character normally erases its WHOLE line for the document-
level RS tier above (the line's CRC fails, so every byte the line carries
becomes an erasure). When ``nsym_line`` is non-zero, each line additionally
carries ``nsym_line`` Reed-Solomon parity bytes computed over
``idx_token.encode() + <the raw bytes that line's payload encodes>`` and
rendered in the line's own alphabet, between the payload and the check field
(the parity field itself is NOT covered by the check). On a CRC failure,
decode treats the printed token+payload+line-parity as one small RS
codeword, blind-corrects up to ``nsym_line // 2`` byte errors (no known
erasure positions -- this tier runs before anything is treated as an
erasure), re-renders the corrected token/payload, and RECOMPUTES the check
field: only if that recomputed check matches the originally printed one is
the correction trusted. This keeps the "recoverable" property of the check
field intact -- an RS miscorrection almost never reproduces the original
16-bit CRC by chance -- while turning many single/double-character line
errors into a silent, free repair that never touches the document-level RS
erasure budget at all. Lines the in-line tier cannot verify fall through
to the existing repair/erasure tiers unchanged.

Decode oracle discipline (CRITICAL)
-----------------------------------
Correctness is judged SOLELY by the per-line CRC check and RS correction. There
is deliberately no "try confusable substitutions and keep whatever decompresses
further" search anywhere in this module. If a line's CRC fails AND RS cannot
correct the resulting erasures, ``decode`` RAISES a clear exception naming the
exact failing line label. It never guesses or mutates data to make it "work".

OCR-confidence erasure hint (``char_conf``, does NOT weaken the above)
------------------------------------------------------------------------
:meth:`Base16GCodec.decode_spool` optionally accepts ``char_conf``: per-line
OCR character confidence, keyed by PHYSICAL LINE ORDER within the encoded
spool (not by the printed index, which may itself be corrupt on a CRC-failed
line). Today, a CRC-failed line contributes its ENTIRE byte span as document-
level Reed-Solomon erasures, even though the typical cause is one or two
misread characters -- wasting the erasure budget ~line-width-fold. When
``char_conf`` names specific low-confidence character positions within an
otherwise CRC-failed line (fewer than ``max_suspects``), only the bytes those
characters map to are marked as erasures; the line's other ("soft") bytes
enter the RS stream as ordinary, unverified data instead of being zeroed.

This is a HINT about WHERE to erase, never about WHAT the bytes are or
whether a line is accepted -- it changes erasure *positions* only.
Acceptance is unchanged: a block still must satisfy Reed-Solomon's erasure/
error budget, and the whole-document SHA-256 gate (``restore/decode.py``) is
still the final oracle. A two-pass, block-local safety valve makes the
optimization strictly no-worse than today: if a block still fails RS with
the narrower char-level erasures, that block alone is retried with the
CRC-failed line(s) it touches erased across their FULL span (today's
behaviour) before the block is given up as uncorrectable. See
``_assemble_to_spool`` and ``Base16GCodec._decode_hardened_spool``.
"""

import collections as _collections
import mmap as _mmap
import io as _io
import tempfile as _tempfile
import typing as _ty

import reedsolo as _reedsolo

from ._base import Codec

__all__ = [
    "ALPHABET",
    "CodecError",
    "Base16GCodec",
    "nibble_encode",
    "nibble_decode",
    "encoded_line_count",
    "describe_line_stream",
    "StreamShape",
    "align_payload_char_conf",
    "DEFAULT_CONF_THRESHOLD",
    "DEFAULT_MAX_SUSPECTS",
]

#: Default OCR confidence threshold (plan 3): a payload character below this
#: is treated as suspect. Chosen conservatively pending a calibration run
#: (see ``tools/conf_calibration.py``); it only affects which bytes decode
#: marks as erasures, never whether a correction is accepted.
DEFAULT_CONF_THRESHOLD: _ty.Final[float] = 0.6

#: Default cap on the number of suspect characters a CRC-failed line may have
#: before decode gives up on char-level erasure marking and falls back to
#: erasing the line's whole span (today's behaviour) -- a line with more
#: suspects than this is not "mostly right with a couple of typos" anymore.
DEFAULT_MAX_SUSPECTS: _ty.Final[int] = 6

# ---------------------------------------------------------------------------
# The measured-safe 16-character alphabet (4 bits/char)
# ---------------------------------------------------------------------------

#: OCR-verified alphabet: measured safe 16/16 (0% char error, 0% line-insertion
#: rate, zero corrupting confusions) on Courier 8pt @ 300 DPI / Tesseract 5.4.0
#: (``tools/ocr_font_report.py``; see the module docstring and public OCR
#: guide). 16 characters = exactly 4 bits/char = clean nibble
#: packing. Do not hand-edit this without re-running the measurement tool.
ALPHABET: _ty.Final[str] = "ABCDHKLMPRTVXY34"

# Decode map: canonical chars, case-insensitive. Built once at import time.
#
# No confusable ALIASES beyond case-folding are added, by design. The excluded
# confusable digits/letters (0, 1, 8, 9, I, J, O, Q, S, U, W, Z, ...) are simply
# rejected here -- a rejected character fails that line's CRC and becomes an RS
# erasure, which is recoverable. An alias is only safe when its source
# character is *not itself* printable by this alphabet (otherwise it is the
# exact "Q -> O -> alias -> 0" silent-corruption bug this alphabet was chosen
# to eliminate) -- but this alphabet was measured at 16/16 safe with 0 corrupting
# confusions and 0% line-insertion *without* any alias, so there is no measured
# need for one, and adding a speculative, unmeasured alias would only reintroduce
# the class of bug being fixed. When in doubt, omit the alias.
_DECODE_MAP: _ty.Final[_ty.Dict[str, int]] = {}
for _i, _ch in enumerate(ALPHABET):
    _DECODE_MAP[_ch] = _i
    _DECODE_MAP[_ch.lower()] = _i
del _i, _ch


# ---------------------------------------------------------------------------
# Radix specification — the ONLY bits/char-dependent parameters. Everything else
# in this module (RS coding, header, spooling) is byte-level and radix-agnostic.
# A codec is one _RadixSpec + the shared pipeline. base16c is the spec below;
# denser codecs (base32g/base64) are other specs (see codec/radix.py).
# ---------------------------------------------------------------------------


def _build_decode_map(alphabet: str) -> _ty.Dict[str, int]:
    # Case-folding is only safe when the alphabet is single-case: if the same
    # letter appears in BOTH cases (e.g. base64 has 'A'=0 and 'a'=26), folding
    # would alias two DISTINCT values together and silently corrupt decode. So
    # add the lowercase alias only for a single-case alphabet.
    letters = [c for c in alphabet if c.isalpha()]
    # Single-case iff folding letters to lowercase loses no distinctions.
    single_case = len({c.lower() for c in letters}) == len(letters)
    m: _ty.Dict[str, int] = {}
    for i, ch in enumerate(alphabet):
        m[ch] = i
        if single_case:
            m[ch.lower()] = i  # case-insensitive; no confusable aliases (see note)
    return m


class _RadixSpec:
    """Immutable per-codec parameters for byte<->char conversion + framing.

    Two packing strategies (``packing``):

    - ``"bits"`` (default): each char carries ``bits = log2(radix)`` bits; the
      alphabet MUST be a power of two. This is base8/16g/32*/64.
    - ``"group"``: Ascii85-style GROUP packing — every ``group_bytes`` bytes map
      to ``group_chars`` base-``radix`` digits (``radix ** group_chars >=
      256 ** group_bytes``). Used for non-power-of-two radices (base85/z85/
      base-maxg) so the fractional bit is not wasted.

    ``check_width`` = chars holding the 16-bit CRC. ``index_width``/``index_mask``
    frame the per-line index (mask defeats OCR phantom-insertion into uniform runs).
    In group mode ``bits`` is the floor bits/char (``floor(log2(radix))``), used
    only for index/check digit math, not for payload packing.
    """

    __slots__ = (
        "name", "alphabet", "radix", "bits", "mask", "check_width", "index_width",
        "index_mask", "decode_map", "max_idx", "case_fold",
        "packing", "group_bytes", "group_chars", "delimiter",
    )

    def __init__(self, name, alphabet, bits, check_width, index_width, index_mask,
                 packing="bits", group_bytes=0, group_chars=0, delimiter="#"):
        radix = len(alphabet)
        self.packing = packing
        # The frame check-field delimiter (``payload <delim>check``) MUST NOT be
        # a payload glyph, or split_frame cannot locate the check field. '#' is
        # the default (outside base16g/32g); base85/z85 include '#' so they pick
        # a free char.
        if len(delimiter) != 1 or delimiter in alphabet or delimiter.isspace():
            raise ValueError(
                f"{name}: delimiter {delimiter!r} must be one non-space char "
                f"outside the alphabet"
            )
        self.delimiter = delimiter
        if packing == "bits":
            if radix != (1 << bits):
                raise ValueError(f"{name}: alphabet length {radix} != 2**{bits}")
            self.mask = (1 << bits) - 1
        elif packing == "group":
            if radix < 2:
                raise ValueError(f"{name}: group alphabet needs >= 2 chars")
            if radix ** group_chars < 256 ** group_bytes:
                raise ValueError(
                    f"{name}: group {group_bytes}->{group_chars} cannot hold the bytes"
                    f" ({radix}**{group_chars} < 256**{group_bytes})"
                )
            self.mask = 0  # unused in group mode
        else:
            raise ValueError(f"{name}: unknown packing {packing!r}")
        if len(index_mask) != index_width:
            raise ValueError(f"{name}: index_mask needs {index_width} values")
        if any(not (0 <= v < radix) for v in index_mask):
            raise ValueError(f"{name}: index_mask values must be in [0,{radix})")
        self.name = name
        self.alphabet = alphabet
        self.radix = radix
        self.bits = bits
        self.check_width = check_width
        self.index_width = index_width
        self.index_mask = tuple(index_mask)
        self.decode_map = _build_decode_map(alphabet)
        self.max_idx = radix ** index_width
        self.group_bytes = group_bytes
        self.group_chars = group_chars
        # Case-folding OCR drift (.upper()) is only valid for a single-case
        # alphabet; a case-significant one (base64) must compare verbatim.
        _letters = [c for c in alphabet if c.isalpha()]
        self.case_fold = len({c.lower() for c in _letters}) == len(_letters)


#: The shipped base16c spec. Its constants reproduce the historical values
#: exactly, so every base16c-bound wrapper below is byte-for-byte unchanged.
BASE16G: _ty.Final["_RadixSpec"] = _RadixSpec(
    name="base16g-crc16-rs",
    alphabet=ALPHABET,
    bits=4,
    check_width=4,
    index_width=5,
    index_mask=(7, 13, 2, 11, 4),
)


class CodecError(ValueError):
    """Raised when a line fails its CRC and RS cannot correct it.

    Subclasses :class:`ValueError` so callers may catch either. The message
    always names the exact failing line label (e.g. ``L00042``).
    """


def _group_encode(data: bytes, spec: "_RadixSpec") -> str:
    """Ascii85-style group packing: ``group_bytes`` bytes -> ``group_chars`` digits.

    Each full group reads big-endian, emits ``group_chars`` base-``radix`` digits
    MSB-first. A final partial group of ``k`` bytes emits ``ceil(8k/log2(radix))``
    digits (the minimum that round-trips ``k`` bytes). Not self-delimiting.
    """
    if not data:
        return ""
    nb, nc, alphabet, radix = spec.group_bytes, spec.group_chars, spec.alphabet, spec.radix
    import math

    out: _ty.List[str] = []
    for start in range(0, len(data), nb):
        chunk = data[start:start + nb]
        value = int.from_bytes(chunk, "big")
        chars = nc if len(chunk) == nb else math.ceil(len(chunk) * 8 / math.log2(radix))
        digits = [""] * chars
        for i in range(chars - 1, -1, -1):
            value, rem = divmod(value, radix)
            digits[i] = alphabet[rem]
        out.append("".join(digits))
    return "".join(out)


def _group_decode(text: str, byte_len: int, spec: "_RadixSpec") -> bytes:
    """Inverse of :func:`_group_encode`; returns exactly ``byte_len`` bytes."""
    if byte_len == 0:
        return b""
    nb, nc, decode_map, radix = spec.group_bytes, spec.group_chars, spec.decode_map, spec.radix
    import math

    out = bytearray()
    pos = 0
    text = text.strip()
    while len(out) < byte_len:
        remaining = byte_len - len(out)
        this_bytes = min(nb, remaining)
        chars = nc if this_bytes == nb else math.ceil(this_bytes * 8 / math.log2(radix))
        group = text[pos:pos + chars]
        pos += chars
        if len(group) < chars:
            raise ValueError(f"payload too short: got {len(out)} bytes, need {byte_len}")
        value = 0
        for ch in group:
            try:
                value = value * radix + decode_map[ch]
            except KeyError:
                raise ValueError(f"invalid alphabet character {ch!r}") from None
        out.extend(value.to_bytes(this_bytes, "big"))
    return bytes(out[:byte_len])


def radix_encode(data: bytes, spec: "_RadixSpec" = BASE16G) -> str:
    """Encode raw bytes to an alphabet string per ``spec`` (bit- or group-packing).

    Bit-packing: ``spec.bits`` bits per char, MSB-first, final group zero-padded.
    Group-packing: Ascii85-style (see :func:`_group_encode`). Not self-delimiting;
    the caller tracks the original byte length (:func:`radix_decode`).
    """
    if spec.packing == "group":
        return _group_encode(data, spec)
    if not data:
        return ""
    bits = spec.bits
    mask = spec.mask
    alphabet = spec.alphabet
    out: _ty.List[str] = []
    acc = 0
    nbits = 0
    for byte in data:
        acc = (acc << 8) | byte
        nbits += 8
        while nbits >= bits:
            nbits -= bits
            out.append(alphabet[(acc >> nbits) & mask])
    if nbits:  # flush remaining bits, left-aligned (MSB-first) into one char
        out.append(alphabet[(acc << (bits - nbits)) & mask])
    return "".join(out)


def radix_decode(text: str, byte_len: int, spec: "_RadixSpec" = BASE16G) -> bytes:
    """Decode an alphabet string back to exactly ``byte_len`` bytes (bit/group).

    Case-insensitive. No confusable aliases are applied (see the comment above
    ``_DECODE_MAP``): any character outside the alphabet raises ``ValueError``
    rather than being guessed at. Trailing pad bits beyond ``byte_len`` bytes
    are discarded.
    """
    if spec.packing == "group":
        return _group_decode(text, byte_len, spec)
    if byte_len == 0:
        return b""
    bits = spec.bits
    decode_map = spec.decode_map
    acc = 0
    nbits = 0
    out = bytearray()
    for ch in text:
        try:
            val = decode_map[ch]
        except KeyError:
            raise ValueError(f"invalid alphabet character {ch!r}") from None
        acc = (acc << bits) | val
        nbits += bits
        if nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
        if len(out) == byte_len:
            break
    if len(out) < byte_len:
        raise ValueError(
            f"payload too short: got {len(out)} bytes, need {byte_len}"
        )
    return bytes(out)


# Base16c-bound public aliases (layout.py + tests import these names).
def nibble_encode(data: bytes) -> str:
    """base16c-bound :func:`radix_encode` (4 bits/char). Public API."""
    return radix_encode(data, BASE16G)


def nibble_decode(text: str, byte_len: int) -> bytes:
    """base16c-bound :func:`radix_decode` (4 bits/char). Public API."""
    return radix_decode(text, byte_len, BASE16G)


# ---------------------------------------------------------------------------
# CRC-16/CCITT (poly 0x1021, init 0xFFFF) and the 4-char check field
# ---------------------------------------------------------------------------

def _crc16_table() -> _ty.Tuple[int, ...]:
    table = []
    for b in range(256):
        crc = b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return tuple(table)


_CRC16_TABLE: _ty.Final[_ty.Tuple[int, ...]] = _crc16_table()


def _crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout."""
    crc = 0xFFFF
    table = _CRC16_TABLE
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ table[((crc >> 8) ^ byte) & 0xFF]
    return crc


#: Number of alphabet characters in a line's check field. At 4 bits/char,
#: 4 chars = exactly 16 bits -- the full width of a CRC-16, with no truncation
#: and no wasted bits (2 chars = 8 bits would not hold it). Documented at
#: module top under "Why 4 check characters, and why 4 is exact now".
CHECK_WIDTH: _ty.Final[int] = 4


def _check_chars(
    kind: str, idx_token: str, payload: str, spec: "_RadixSpec" = BASE16G
) -> str:
    """Compute the check field (``spec.check_width`` chars) for a framed line.

    The CRC covers exactly what is *printed* -- the kind letter, the index
    token, and the payload characters -- so a human can recompute it straight
    off the page. Covering ``kind`` closes the v1 gap where a misread
    ``L``<->``P`` (or ``H``/``T``/``Q``) kind letter produced a CRC-VALID
    phantom line at the same index: the kind letter is now part of what the
    16-bit CRC actually protects, so a kind flip fails the check like any
    other single-character error. The CRC is rendered MSB-first across
    ``check_width`` chars of ``spec.bits`` bits; when ``check_width * bits >
    16`` (e.g. base8), the top pad bits are zero.
    """
    crc = _crc16_ccitt(kind.encode() + idx_token.encode() + payload.encode())
    alphabet = spec.alphabet
    if spec.packing == "bits":
        bits, mask = spec.bits, spec.mask
        return "".join(
            alphabet[(crc >> (bits * shift)) & mask]
            for shift in range(spec.check_width - 1, -1, -1)
        )
    # group / arbitrary radix: render the 16-bit CRC as base-radix digits.
    radix = spec.radix
    digits = [0] * spec.check_width
    v = crc
    for i in range(spec.check_width - 1, -1, -1):
        v, digits[i] = divmod(v, radix)
    return "".join(alphabet[d] for d in digits)


# ---------------------------------------------------------------------------
# Line framing
# ---------------------------------------------------------------------------

#: Printed width of a line's index field, in alphabet characters.
#:
#: At 4 bits/char (vs. the prior alphabet's 5), one more character is needed to
#: keep roughly the same per-stream line capacity: the prior ``INDEX_WIDTH=4``
#: at 32 chars/digit gave a cap of ``32**4 = 1,048,575`` lines. Widening to 5
#: at 16 chars/digit gives ``16**5 = 1,048,576`` -- capacity does not shrink
#: just because the alphabet did. (Staying at 4 would have capped a stream at
#: ``16**4 = 65,536`` lines, a 16x drop -- not enough headroom to keep this an
#: implementation detail rather than a user-visible size limit.)
INDEX_WIDTH: _ty.Final[int] = 5

# Fixed, public per-position mask XORed into each 4-bit nibble of the index
# before it is rendered. Its only job is to guarantee the index never prints as
# a run of identical glyphs: OCR engines reliably *insert* phantom characters
# into uniform runs, and a decimal index made every line start with `00000` --
# the single worst target on the page. The prior 4-char/5-bit mask scored
# 12/12 vs 5/12 for decimal and 3/12 for unmasked Crockford (see git history);
# this is the same masking technique re-pinned
# to 5 chars of 4-bit nibbles for the new alphabet. All 5 values are distinct
# 4-bit constants, which is what defeats the runs that are actually hit by
# small (real-world) indices -- see the no-uniform-run test over 0..5000.
_INDEX_MASK: _ty.Final[_ty.Tuple[int, ...]] = (7, 13, 2, 11, 4)

_MAX_IDX: _ty.Final[int] = 16**INDEX_WIDTH  # index is INDEX_WIDTH alphabet chars
_RS_BLOCK: _ty.Final[int] = 255  # GF(2^8) code length ceiling


def _encode_index(idx: int, spec: "_RadixSpec") -> str:
    """Render a line index as its printed, OCR-safe alphabet token (per spec).

    Never returns a run of identical characters, for any index in the tested
    range (0..5000; see the no-uniform-run test), thanks to ``spec.index_mask``.
    Bit-packing masks the base-``radix`` digit with XOR (radix is a power of two,
    so the result stays in range); group-packing (non-power-of-two radix) uses a
    modular offset, which is likewise invertible and in-range.
    """
    alphabet = spec.alphabet
    width = spec.index_width
    imask = spec.index_mask
    radix = spec.radix
    if spec.packing == "bits":
        bits, mask = spec.bits, spec.mask
        return "".join(
            alphabet[((idx >> (bits * shift)) & mask) ^ imask[width - 1 - shift]]
            for shift in range(width - 1, -1, -1)
        )
    # group / arbitrary radix: base-radix digits with a modular offset mask.
    digits = [0] * width
    v = idx
    for i in range(width - 1, -1, -1):
        v, digits[i] = divmod(v, radix)
    return "".join(alphabet[(digits[i] + imask[i]) % radix] for i in range(width))


def _decode_index(token: str, spec: "_RadixSpec") -> _ty.Optional[int]:
    """Inverse of :func:`_encode_index` (per spec); ``None`` if unreadable.

    Case-insensitive; no confusable aliases (see ``_DECODE_MAP``). A wrong
    result cannot slip through: the per-line CRC covers the printed token.
    """
    if len(token) != spec.index_width:
        return None
    decode_map = spec.decode_map
    imask = spec.index_mask
    radix = spec.radix
    value = 0
    if spec.packing == "bits":
        bits = spec.bits
        for position, char in enumerate(token):
            try:
                digit = decode_map[char]
            except KeyError:
                return None
            value = (value << bits) | (digit ^ imask[position])
        return value
    for position, char in enumerate(token):
        try:
            digit = decode_map[char]
        except KeyError:
            return None
        value = value * radix + ((digit - imask[position]) % radix)
    return value


def encode_index(idx: int) -> str:
    """base16c-bound :func:`_encode_index`. Public API.

    ``0`` -> ``MYCVH``, ``1`` -> ``MYCVK``, ``1048575`` -> ``PCYHV``.
    """
    return _encode_index(idx, BASE16G)


def decode_index(token: str) -> _ty.Optional[int]:
    """base16c-bound :func:`_decode_index`. Public API."""
    return _decode_index(token, BASE16G)


def _line_parity_chars(nsym_line: int, spec: "_RadixSpec") -> int:
    """Printed width of the optional line-parity field, in alphabet characters."""
    if nsym_line <= 0:
        return 0
    if spec.packing == "group":
        import math

        return math.ceil(nsym_line * 8 / math.log2(spec.radix))
    return -(-(nsym_line * 8) // spec.bits)  # ceil(nsym_line*8 / spec.bits)


def _line_rs_codeword(token: str, chunk: bytes) -> bytes:
    """The bytes a line's per-line Reed-Solomon parity is computed over."""
    return token.encode("ascii") + chunk


def _nsym_line_for_chars(line_parity_chars: int, spec: "_RadixSpec") -> int:
    """Invert :func:`_line_parity_chars`: the printed width -> ``nsym_line`` bytes.

    Only 0, 2, and 4 are ever encoded (:meth:`Base16GCodec.encode` validates
    this), so this maps a detected width back to whichever of those three
    values would have produced it, defaulting to 0 (no line-parity field) for
    a width that matches none -- decode's header cross-check is the actual
    authority; this is only the structural pre-header guess.
    """
    if line_parity_chars <= 0:
        return 0
    for candidate in (2, 4):
        if _line_parity_chars(candidate, spec) == line_parity_chars:
            return candidate
    return 0


def _frame(
    kind: str,
    idx: int,
    chunk: bytes,
    spec: "_RadixSpec" = BASE16G,
    *,
    nsym_line: int = 0,
) -> str:
    """Render one printed line: ``<kind><idx> <payload> [<line-parity>] #<check>``.

    ``chunk`` is the raw bytes this line carries (its ``radix_encode`` is the
    printed payload). When ``nsym_line`` is non-zero, ``nsym_line`` Reed-
    Solomon parity bytes are computed over ``idx_token + chunk`` and rendered
    as an extra glyph-run between the payload and the check field; the check
    field itself covers ``kind + idx_token + payload`` only (the line-parity
    field is deliberately NOT covered -- see the module docstring's "Per-line
    Reed-Solomon" section).
    """
    token = _encode_index(idx, spec)
    payload = radix_encode(chunk, spec)
    fields = [f"{kind}{token}", payload]
    if nsym_line > 0:
        codec = _reedsolo.RSCodec(nsym_line)
        message = _line_rs_codeword(token, chunk)
        parity = bytes(codec.encode(message)[len(message):])
        fields.append(radix_encode(parity, spec))
    check = _check_chars(kind, token, payload, spec)
    fields.append(f"{spec.delimiter}{check}")
    return " ".join(fields)


class _ParsedLine(_ty.NamedTuple):
    kind: str  # "L" or "P"
    idx: int
    payload: str
    line_parity: _ty.Optional[str]  # printed line-parity token, or None
    ok: bool  # True iff the CRC check field matched


def split_frame(
    line: str,
    *,
    allow_trailing: bool = False,
    spec: "_RadixSpec" = BASE16G,
    line_parity_chars: int = 0,
) -> _ty.Optional[_ty.Tuple[str, str, str]]:
    """Structurally split a printed line into ``(label, payload, check)``.

    OCR (observed: Tesseract) sometimes inserts a spurious space *inside* the
    payload (e.g. ``...FYWZQH4 6F1IWO0C...``), which would turn the intended 3
    whitespace tokens into 4+ and cause a naive ``line.split()`` shape test to
    discard an otherwise-perfect line. The frame's actual shape is not "exactly
    3 tokens" -- it is "a label, then a payload [then a line-parity run], then
    a ``#check`` field": the label is always the *first* token and ``#check``
    is always the *last* token, so everything in between is payload (+
    optionally line-parity).

    This is deterministic normalization, not guessing: the payload alphabet
    contains no whitespace, so any interior space is provably OCR noise, and
    joining the middle tokens with no separator recovers exactly the printed
    payload (+ line-parity) characters. The per-line CRC (computed downstream
    over this recovered payload) is what actually decides correctness -- this
    function only ensures a noisy-but-readable line reaches that check instead
    of being silently dropped before it gets the chance.

    ``line_parity_chars`` (0 by default -- no line-parity field): when > 0,
    the trailing ``line_parity_chars`` characters of the joined middle run are
    split off as a fourth field; the returned ``payload`` is the remainder
    (positional split from the end, since interior OCR spaces mean the
    payload/line-parity boundary cannot be found by whitespace alone -- see
    the module docstring's frame grammar). The returned tuple's second element
    stays ``payload`` only for callers that pass ``line_parity_chars=0``; when
    it is > 0, use :func:`split_frame_with_parity` for the 4-tuple form.

    Returns ``None`` if the line has fewer than 3 tokens or the last token
    does not start with ``#`` (i.e. it does not have the frame shape at all --
    e.g. the ``PAGE 1/1 sha256=...`` footer, which is 3 tokens but whose last
    token is not a check field).
    """
    result = split_frame_with_parity(
        line,
        allow_trailing=allow_trailing,
        spec=spec,
        line_parity_chars=line_parity_chars,
    )
    if result is None:
        return None
    label, payload, line_parity, check = result
    if line_parity_chars:
        return label, payload + line_parity, check
    return label, payload, check


def split_frame_with_parity(
    line: str,
    *,
    allow_trailing: bool = False,
    spec: "_RadixSpec" = BASE16G,
    line_parity_chars: int = 0,
) -> _ty.Optional[_ty.Tuple[str, str, str, str]]:
    """Like :func:`split_frame` but returns ``(label, payload, line_parity, check)``.

    ``line_parity`` is ``""`` when ``line_parity_chars`` is 0. When positive,
    the middle glyph run is split positionally from the end: the LAST
    ``line_parity_chars`` characters are the line-parity field, everything
    before that is payload. This is exact (not a guess) because both fields
    are drawn from the same alphabet at fixed, known widths -- there is no
    delimiter between them, by design (one glyph run, split by position).
    """
    delim = spec.delimiter
    parts = line.split()
    middle = None
    if len(parts) >= 3:
        check_positions = [
            index for index, part in enumerate(parts[1:], 1)
            if part.startswith(delim)
        ]
        if len(check_positions) == 1:
            position = check_positions[0]
            if allow_trailing or position == len(parts) - 1:
                middle = "".join(parts[1:position])
                label, check = parts[0], parts[position]

    if middle is None:
        # A constrained Tesseract whitelist can remove both printed separator
        # spaces while preserving every protected glyph.  The label and check
        # have fixed widths, and the delimiter is outside the payload
        # alphabet, so this compact shape remains unambiguous and still flows
        # through the CRC check.
        stripped = line.strip()
        label_width = spec.index_width + 1
        if len(stripped) < label_width + 1 + spec.check_width:
            return None
        label = stripped[:label_width]
        remainder = stripped[label_width:]
        if remainder.count(delim) != 1:
            return None
        marker = remainder.index(delim)
        check_end = marker + 1 + spec.check_width
        if marker == 0 or check_end > len(remainder):
            return None
        trailing = remainder[check_end:]
        if trailing and not allow_trailing:
            return None
        middle = "".join(remainder[:marker].split())
        check = remainder[marker:check_end]

    if line_parity_chars:
        if len(middle) < line_parity_chars:
            return None
        payload = middle[:-line_parity_chars]
        line_parity = middle[-line_parity_chars:]
    else:
        payload = middle
        line_parity = ""
    return label, payload, line_parity, check


def align_payload_char_conf(
    line: str,
    char_conf: _ty.Sequence[_ty.Optional[float]],
    spec: "_RadixSpec" = BASE16G,
    *,
    line_parity_chars: int = 0,
) -> _ty.Optional[_ty.List[_ty.Optional[float]]]:
    """Slice a raw line's per-character OCR confidence down to its payload.

    ``char_conf`` must be the same length as ``line`` (one confidence value
    per printed character, spaces included -- providers give whitespace a
    confidence of ``1.0``). This mirrors :func:`split_frame_with_parity`'s
    POSITIONAL "compact" derivation: label (``spec.index_width + 1`` chars)
    then payload+line-parity (everything up to the ``spec.delimiter``) then
    ``#check``. Both branches of ``split_frame_with_parity`` reduce to this
    same shape once interior OCR whitespace is stripped, so applying it
    uniformly here (rather than re-running the token-based branch) recovers
    the same payload boundary for any line that actually has the frame
    shape. Keep this in sync with :func:`split_frame_with_parity` if that
    function's positional math changes.

    Returns ``None`` (never guesses) if ``char_conf`` is the wrong length,
    the line does not have the frame shape, or the recovered payload region
    doesn't have a sane width -- callers must treat that as "no confidence
    available" and fall back to whole-line erasure marking.
    """
    if len(char_conf) != len(line):
        return None
    compact_chars: _ty.List[str] = []
    compact_conf: _ty.List[_ty.Optional[float]] = []
    for ch, cf in zip(line, char_conf):
        if not ch.isspace():
            compact_chars.append(ch)
            compact_conf.append(cf)
    compact = "".join(compact_chars)
    label_width = spec.index_width + 1
    if len(compact) < label_width + 1 + spec.check_width:
        return None
    delim = spec.delimiter
    remainder = compact[label_width:]
    if remainder.count(delim) != 1:
        return None
    marker = remainder.index(delim)
    check_end = marker + 1 + spec.check_width
    if marker == 0 or check_end > len(remainder):
        return None
    middle_conf = compact_conf[label_width:label_width + marker]
    if line_parity_chars:
        if len(middle_conf) < line_parity_chars:
            return None
        return middle_conf[:-line_parity_chars]
    return middle_conf


def _suspect_byte_offsets(
    positions: _ty.Iterable[int], span: int, spec: "_RadixSpec"
) -> _ty.Set[int]:
    """Map low-confidence PAYLOAD CHARACTER positions to byte offsets.

    Bit-packing: character ``pos`` lives in byte ``pos * spec.bits // 8``
    (floor -- ``spec.bits`` bits/char, ``8 // spec.bits`` chars/byte).
    Group-packing: a byte is only determined jointly with the rest of its
    ``group_bytes``-byte group, so any suspect character inside a group
    marks the WHOLE group. Offsets beyond ``span`` (e.g. a suspect pad
    character the payload's own width doesn't carry a byte for) are
    dropped, not clamped -- there is no byte there to erase.
    """
    offsets: _ty.Set[int] = set()
    if spec.packing == "group":
        for pos in positions:
            start = (pos // spec.group_chars) * spec.group_bytes
            offsets.update(b for b in range(start, start + spec.group_bytes) if b < span)
    else:
        for pos in positions:
            offset = (pos * spec.bits) // 8
            if offset < span:
                offsets.add(offset)
    return offsets


def _parse_line(
    line: str, spec: "_RadixSpec" = BASE16G, *, line_parity_chars: int = 0
) -> _ty.Optional[_ParsedLine]:
    """Parse one framed line. Returns None for blank/foreign lines.

    ``ok`` reflects whether the printed check field matches a freshly computed
    CRC over the (alias-normalized) kind+index+payload -- the check field does
    NOT cover the optional line-parity field (see the module docstring). A
    structurally broken line (missing fields, bad kind) is treated as a failed
    check on a best-effort index so decode can still localize it; if even the
    index is unreadable the line is skipped (its absence surfaces later as a
    length/erasure error).

    ``line_parity_chars`` (0 by default) is the printed width of the optional
    line-parity field for this stream; the caller determines it once per
    decode (structurally, from the modal per-line token/character shape) and
    passes it consistently for every line of that stream -- see
    :func:`_detect_line_parity_chars`.

    The line is split via :func:`split_frame_with_parity`, which anchors the
    label as the first token and ``#check`` as the last, joining everything
    between as payload (+ line-parity) -- this tolerates OCR-inserted interior
    spaces (see its docstring).
    """
    stripped = line.strip()
    if not stripped:
        return None
    split = split_frame_with_parity(
        stripped, spec=spec, line_parity_chars=line_parity_chars
    )
    if split is None:
        return None
    label, payload, line_parity, check = split
    kind = label[:1]
    if kind not in ("L", "P"):
        return None
    idx_token = label[1:]
    idx = _decode_index(idx_token, spec)
    if idx is None:
        return None
    # The CRC covers the printed token, so a misread index fails its own check and
    # becomes an erasure -- it can never smuggle a bad line through. OCR case
    # drift is normalized with .upper() ONLY for a single-case alphabet; a
    # case-significant one (base64) must compare the printed characters verbatim.
    fold = spec.case_fold
    token_for_crc = idx_token.upper() if fold else idx_token
    check_chars = check[1:]
    expected = _check_chars(kind, token_for_crc, payload, spec)
    ok = (check_chars.upper() if fold else check_chars) == expected
    return _ParsedLine(
        kind=kind,
        idx=idx,
        payload=payload,
        line_parity=line_parity if line_parity_chars else None,
        ok=ok,
    )


def _detect_line_parity_chars(
    sample_lines: _ty.Iterable[str], spec: "_RadixSpec"
) -> int:
    """Structurally detect the printed line-parity field width from raw lines.

    A clean frame line is exactly 3 whitespace tokens (``label payload
    #check``) with no line-parity field, or 4 tokens (``label payload
    line_parity #check``) with one. This counts whitespace-token counts across
    a sample of L/P-shaped lines (label starts with L or P, last token starts
    with the delimiter) and returns the modal count translated to a character
    width: 0 for 3-token lines, or the modal LENGTH of the third token for
    4-token lines. Ties and ambiguous/empty samples resolve to 0 (no
    line-parity field) -- the header cross-check (decode's actual authority)
    catches a wrong guess rather than this heuristic silently mis-framing
    every line.

    A line with ALL interior whitespace removed (the "compact frame" OCR
    class ``split_frame`` also tolerates) has exactly one token and cannot
    vote here: the fixed label/check widths alone cannot distinguish "wide
    payload, no line-parity" from "narrower payload plus a line-parity field"
    without a separator, so a transcript sampled entirely from compact lines
    is genuinely ambiguous by this heuristic and falls back to 0 -- the header
    cross-check is the backstop for that case.
    """
    delim = spec.delimiter
    counts: _ty.Counter[int] = _collections.Counter()
    for line in sample_lines:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) not in (3, 4):
            continue
        if parts[0][:1] not in ("L", "P") or not parts[-1].startswith(delim):
            continue
        if len(parts) == 3:
            counts[0] += 1
        else:
            counts[len(parts[2])] += 1
    if not counts:
        return 0
    return counts.most_common(1)[0][0]


def repair_line(
    line: str, spec: "_RadixSpec" = BASE16G, *, line_parity_chars: int = 0
) -> _ty.Optional[str]:
    """Attempt to repair a single misread character in a CRC-failed line.

    The per-line CRC is the *oracle*: we try every single-character
    substitution of the printed ``kind + index_token + payload`` body (kind is
    included because the CRC now covers it too -- a kind flip is just another
    single-character error) and the case where the printed check field itself
    is the corrupted character, recompute the CRC for each candidate, and
    accept a repair **only if exactly one candidate reproduces the printed
    check** (or, for a corrupted check field, the recomputed check is within
    one character of the printed one). Ambiguity or zero hits leaves the line
    untouched (the caller keeps it as an erasure).

    This does not violate the decode-discipline doctrine ("never mutate a
    character merely because more compressed bytes become readable"): acceptance
    is CRC-verified, not decompressibility-driven. A false unique candidate
    requires a >=2-error line, a spurious CRC match (~2**-16), and no colliding
    true candidate; such a line enters as a blind error which document RS still
    corrects within budget, and the whole-document SHA-256 gate backstops it.

    ``line_parity_chars`` (0 by default): the line-parity field (if present) is
    NOT covered by the CRC and is not searched for errors here; it is carried
    through unchanged in the repaired output.

    Returns the repaired printed line (which re-parses as CRC-ok), or ``None``.
    Works for both bit-packed and group-packed specs -- the CRC covers the
    printed characters, so the procedure is alphabet-agnostic.
    """
    split = split_frame_with_parity(
        line.strip(), spec=spec, line_parity_chars=line_parity_chars
    )
    if split is None:
        return None
    label, payload, line_parity, check = split
    kind = label[:1]
    if kind not in ("L", "P"):
        return None
    token = label[1:]
    if len(token) != spec.index_width or not check.startswith(spec.delimiter):
        return None
    want = check[len(spec.delimiter):]
    if len(want) != spec.check_width:
        return None
    fold = spec.case_fold
    want_cmp = want.upper() if fold else want
    body = kind + token + payload
    alphabet = spec.alphabet
    parity_field = f" {line_parity}" if line_parity_chars else ""
    hits: _ty.List[str] = []
    for pos in range(len(body)):
        original = body[pos]
        # The kind character (position 0) is restricted to the two valid
        # kinds, not the whole payload alphabet -- a "kind" character is never
        # a member of the payload alphabet in general (e.g. base32g), and even
        # when it happens to be, only L/P are ever legal there.
        candidates_here = ("L", "P") if pos == 0 else alphabet
        for sub in candidates_here:
            if sub == original:
                continue
            cand = body[:pos] + sub + body[pos + 1:]
            cand_kind = cand[0]
            cand_token = cand[1:1 + len(token)]
            cand_payload = cand[1 + len(token):]
            token_for_crc = cand_token.upper() if fold else cand_token
            if _check_chars(cand_kind, token_for_crc, cand_payload, spec) == want_cmp:
                hits.append(cand)
                if len(hits) > 1:
                    return None  # ambiguous -- refuse to guess
    # Case: the check FIELD was the corrupted character (body is already right).
    token_for_crc = token.upper() if fold else token
    expected = _check_chars(kind, token_for_crc, payload, spec)
    if not hits and expected != want_cmp and _hamming1(expected, want_cmp):
        # Payload/token/kind are intact; only the printed check drifted.
        return f"{kind}{token} {payload}{parity_field} {spec.delimiter}{expected}"
    if len(hits) == 1:
        cand = hits[0]
        cand_kind = cand[0]
        cand_token = cand[1:1 + len(token)]
        cand_payload = cand[1 + len(token):]
        return f"{cand_kind}{cand_token} {cand_payload}{parity_field} {spec.delimiter}{want}"
    return None


def _hamming1(a: str, b: str) -> bool:
    """True iff strings of equal length differ in exactly one position."""
    if len(a) != len(b):
        return False
    return sum(x != y for x, y in zip(a, b)) == 1


def line_rs_correct(
    line: str, spec: "_RadixSpec" = BASE16G, *, nsym_line: int
) -> _ty.Optional[str]:
    """In-line Reed-Solomon correction tier (runs BEFORE :func:`repair_line`).

    For a CRC-failed line carrying a non-empty line-parity field, treat the
    printed ``idx_token + payload`` bytes plus the ``nsym_line`` printed
    parity bytes as one small RS codeword and blind-correct it (no known
    erasure positions -- the line-parity tier runs before anything is
    committed as an erasure). On a successful decode, re-render the corrected
    token/payload and RECOMPUTE the check field: the fix is trusted only if
    that recomputed check reproduces the ORIGINALLY PRINTED one exactly (an RS
    miscorrection essentially never also reproduces a 16-bit CRC by chance).
    Otherwise -- ambiguous RS decode, or a "corrected" line whose check still
    disagrees -- returns ``None`` and the caller falls through to
    :func:`repair_line` and finally to whole-line erasure, unchanged.

    The payload's own printed width determines how many raw bytes it decodes
    to (``len(payload) * spec.bits // 8``, i.e. the line's OWN declared
    shape -- this tier runs before the modal-width vote, so it cannot rely on
    stream-wide bookkeeping). Returns ``None`` immediately if ``nsym_line`` is
    0 (no line-parity field to correct with) or the line does not
    structurally carry one.
    """
    if nsym_line <= 0:
        return None
    split = split_frame_with_parity(
        line.strip(), spec=spec, line_parity_chars=_line_parity_chars(nsym_line, spec)
    )
    if split is None:
        return None
    label, payload, line_parity, check = split
    kind = label[:1]
    if kind not in ("L", "P"):
        return None
    token = label[1:]
    if len(token) != spec.index_width or not check.startswith(spec.delimiter):
        return None
    want = check[len(spec.delimiter):]
    if len(want) != spec.check_width:
        return None
    payload_bytes = _bytes_per_line(len(payload), spec) if payload else 0
    try:
        chunk = radix_decode(payload, payload_bytes, spec)
        line_parity_bytes = radix_decode(line_parity, nsym_line, spec)
    except ValueError:
        return None
    codeword = bytearray(_line_rs_codeword(token, chunk) + line_parity_bytes)
    codec = _reedsolo.RSCodec(nsym_line)
    try:
        decoded = codec.decode(codeword)[0]
    except (_reedsolo.ReedSolomonError, ValueError):
        return None
    corrected_token_bytes = bytes(decoded[:spec.index_width])
    corrected_chunk = bytes(decoded[spec.index_width:])
    try:
        corrected_token = corrected_token_bytes.decode("ascii")
    except UnicodeDecodeError:
        return None
    fold = spec.case_fold
    token_for_crc = corrected_token.upper() if fold else corrected_token
    corrected_payload = radix_encode(corrected_chunk, spec)
    recomputed = _check_chars(kind, token_for_crc, corrected_payload, spec)
    want_cmp = want.upper() if fold else want
    if recomputed != want_cmp:
        return None  # the RS "fix" doesn't reproduce the printed CRC -- reject
    return f"{kind}{corrected_token} {corrected_payload} {line_parity} {spec.delimiter}{want}"


# ---------------------------------------------------------------------------
# Group header (carries exact original length + RS params)
# ---------------------------------------------------------------------------

_MAGIC: _ty.Final[bytes] = b"B1"
_VERSION: _ty.Final[int] = 1
#: magic + ver + nsym + nsym_line + u32(orig_len)
_HEADER_LEN: _ty.Final[int] = len(_MAGIC) + 1 + 1 + 1 + 4


def _make_header(nsym: int, orig_len: int, nsym_line: int = 0) -> bytes:
    return _MAGIC + bytes([_VERSION, nsym, nsym_line]) + orig_len.to_bytes(4, "big")


def _parse_header(data: bytes) -> _ty.Tuple[int, int, int]:
    if len(data) < _HEADER_LEN or data[:2] != _MAGIC:
        raise ValueError("corrupt group header: bad magic or truncated stream")
    if data[2] != _VERSION:
        raise ValueError(f"unsupported codec version {data[2]}")
    nsym = data[3]
    nsym_line = data[4]
    orig_len = int.from_bytes(data[5:9], "big")
    return nsym, orig_len, nsym_line


# ---------------------------------------------------------------------------
# Reed-Solomon block layout
#
# We split the data byte stream into interleaved RS blocks so the total length
# can exceed 255. Block i holds data bytes at positions i, i+B, i+2B, ...  where
# B = number of blocks. Each block is RS-encoded independently with ``nsym``
# parity symbols. Interleaving means a run of consecutive bad payload bytes (a
# whole bad line) spreads its damage across blocks rather than exhausting one
# block's budget — the classic burst-error defence, same as QR.
# ---------------------------------------------------------------------------


def _num_blocks(data_len: int, nsym: int) -> int:
    """Number of interleaved RS blocks so each holds <= 255 data+parity bytes."""
    if data_len == 0:
        return 1
    # each block carries ceil(data_len / B) data bytes + nsym parity <= 255
    max_data_per_block = _RS_BLOCK - nsym
    if max_data_per_block < 1:
        raise ValueError(f"nsym={nsym} leaves no room for data in a 255-byte block")
    return (data_len + max_data_per_block - 1) // max_data_per_block


def _select_nsym(data_len: int, parity_ratio: float) -> int:
    """Choose per-block parity nearest the requested aggregate byte ratio."""
    candidates = []
    for nsym in range(2, 101):
        nblocks = _num_blocks(data_len, nsym)
        parity_len = nblocks * nsym
        actual_ratio = parity_len / data_len
        candidates.append(
            (
                abs(actual_ratio - parity_ratio),
                actual_ratio < parity_ratio,
                parity_len,
                nsym,
            )
        )
    return min(candidates)[-1]


def _candidate_nsym(data_len: int, parity_len: int) -> _ty.List[int]:
    """Candidate ``nsym`` values consistent with the observed stream shapes.

    The encoder guarantees ``parity_len == _num_blocks(data_len, nsym) * nsym``
    for the single ``nsym`` it chose (clamped to 2..100). We invert that: return
    every ``nsym`` in range that reproduces ``parity_len``. Usually the list has
    one element; for tiny inputs several may satisfy the equation, and the caller
    disambiguates by validating each candidate's *corrected* header. Ordered so
    the encoder's greedy packing (fewest blocks, then smallest nsym) is tried
    first. Empty if nothing matches (a dropped/spurious line shifted the layout).
    """
    matches = [
        nsym
        for nsym in range(2, 101)
        if _num_blocks(data_len, nsym) * nsym == parity_len
    ]
    matches.sort(key=lambda n: (_num_blocks(data_len, n), n))
    return matches


def _parity_position(j: int, b: int, nblocks: int) -> int:
    """Symbol-major printed-stream offset of parity byte ``j`` of block ``b``.

    Interleaved (not ``b * nsym + j``, the pre-v2 contiguous layout): parity
    byte ``j`` of every block is grouped together before byte ``j + 1`` of any
    block. A single bad PARITY line (a contiguous run of ``bytes_per_line``
    printed-stream positions) then costs each of the ``nblocks`` blocks at
    most ``ceil(bytes_per_line / nblocks)`` parity symbols instead of wiping
    one block's entire parity budget -- the same burst-spreading the data
    stream already gets from its own interleaving (``data[b::nblocks]``),
    applied to the parity stream. Pure permutation of where each parity byte
    is *printed*; the RS math per block is unchanged.
    """
    return j * nblocks + b


def _rs_encode(data: bytes, nsym: int) -> _ty.Tuple[bytes, bytes, int]:
    """Return (data_bytes, parity_bytes, num_blocks).

    ``parity_bytes`` is INTERLEAVED symbol-major across blocks (see
    :func:`_parity_position`): parity byte ``j`` of block ``b`` lives at
    offset ``j * nblocks + b``, not the contiguous ``b * nsym + j``.
    ``data_bytes`` is returned unchanged (it is the stream the caller frames
    into ``L`` lines).
    """
    nblocks = _num_blocks(len(data), nsym)
    codec = _reedsolo.RSCodec(nsym)
    block_parities: _ty.List[bytes] = []
    for b in range(nblocks):
        block = bytes(data[b::nblocks])  # interleaved stripe
        encoded = codec.encode(block)  # data + nsym parity
        block_parities.append(bytes(encoded[len(block):]))
    parity = bytearray(nsym * nblocks)
    for b in range(nblocks):
        for j in range(nsym):
            parity[_parity_position(j, b, nblocks)] = block_parities[b][j]
    return data, bytes(parity), nblocks


def _rs_decode(
    data: bytearray,
    data_erasures: _ty.List[int],
    parity: bytearray,
    parity_erasures: _ty.List[int],
    nsym: int,
    nblocks: int,
) -> bytes:
    """Correct ``data`` in place using ``parity``; return corrected data bytes.

    ``*_erasures`` are byte positions (within data / within parity) whose source
    line failed CRC — RS treats them as known-position erasures. ``parity`` and
    ``parity_erasures`` use the INTERLEAVED symbol-major layout (see
    :func:`_parity_position`); this reads block ``b``'s ``nsym`` parity bytes
    back out of that layout before handing them to reedsolo. Raises
    ``_reedsolo.ReedSolomonError`` if a block exceeds the correction budget.
    """
    codec = _reedsolo.RSCodec(nsym)
    data_len = len(data)
    parity_len = len(parity)
    data_erasure_set = set(data_erasures)
    parity_erasure_set = set(parity_erasures)
    out = bytearray(data)
    for b in range(nblocks):
        data_pos = list(range(b, data_len, nblocks))  # global positions in stripe
        block_data = bytes(data[b::nblocks])
        parity_pos = [_parity_position(j, b, nblocks) for j in range(nsym)]
        block_parity = bytes(
            parity[pos] if pos < parity_len else 0 for pos in parity_pos
        )
        codeword = bytearray(block_data + block_parity)
        # Map global erasure positions to positions within this codeword.
        erase_pos: _ty.List[int] = []
        for local_i, gpos in enumerate(data_pos):
            if gpos in data_erasure_set:
                erase_pos.append(local_i)
        base = len(block_data)
        for j, pos in enumerate(parity_pos):
            if pos in parity_erasure_set or pos >= parity_len:
                erase_pos.append(base + j)
        decoded = codec.decode(codeword, erase_pos=erase_pos or None)
        corrected = decoded[0]  # data portion only (nsym stripped)
        for local_i, gpos in enumerate(data_pos):
            out[gpos] = corrected[local_i]
    return bytes(out[:data_len])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _bytes_per_line(line_width: int, spec: "_RadixSpec") -> int:
    """Whole bytes carried by a full ``line_width``-char payload line (per spec).

    Bit-packing: ``line_width * bits // 8``. Group-packing: whole groups only,
    ``(line_width // group_chars) * group_bytes``.
    """
    if spec.packing == "group":
        return (line_width // spec.group_chars) * spec.group_bytes
    return (line_width * spec.bits) // 8


def _frame_bytes(
    kind: str,
    data: bytes,
    line_width: int,
    spec: "_RadixSpec" = BASE16G,
    *,
    nsym_line: int = 0,
) -> _ty.List[str]:
    """Encode ``data`` with the alphabet and split into framed lines of ``line_width``.

    Each line's payload maps back to a whole number of bytes: we chunk the byte
    stream so that ``line_width`` alphabet chars carry a fixed byte count where
    possible, and the final line carries whatever remains. To keep byte↔line
    mapping exact and simple, we pack a fixed number of bytes per line such that
    they encode to at most ``line_width`` chars.

    ``nsym_line`` (0 by default) requests a per-line Reed-Solomon parity field
    on each emitted line (see :func:`_frame`); page-parity ``Q`` frames
    (``layout.py``) intentionally pass 0 -- whole-page RS already protects
    them, and they carry no group header to record a per-stream value in.
    """
    if not data:
        return []
    # bytes per full line: the largest B with ceil(8B/spec.bits) <= line_width.
    bytes_per_line = _bytes_per_line(line_width, spec)
    if bytes_per_line < 1:
        raise ValueError("line_width too small to carry a byte")
    lines: _ty.List[str] = []
    idx = 0
    for start in range(0, len(data), bytes_per_line):
        chunk = data[start:start + bytes_per_line]
        if idx >= spec.max_idx:
            raise ValueError(
                f"{kind} stream exceeds {spec.max_idx} lines; use smaller pages"
            )
        lines.append(_frame(kind, idx, chunk, spec, nsym_line=nsym_line))
        idx += 1
    return lines


def _encoding_shape(
    data_len: int,
    line_width: int,
    parity_ratio: float,
    spec: "_RadixSpec" = BASE16G,
    nsym_line: int = 0,
):
    """Compute the encoder's exact byte/line layout ahead of encoding.

    ``nsym_line`` does not change ``bytes_per_line`` or the resulting L/P
    LINE COUNTS -- those are governed purely by ``line_width`` (the PAYLOAD
    width in characters) and the document-level RS ``nsym``/``nblocks``, both
    unaffected by the per-line parity field. It is accepted here (and
    validated) purely so every call site can pass the same ``nsym_line`` it
    encodes with -- :func:`_line_parity_chars` (``ceil(nsym_line*8/spec.bits)``
    characters/line) is what a caller needing the full PRINTED line width
    (payload + line-parity) must add on top of ``line_width`` separately;
    :func:`encoded_line_count`'s job is only the exact line COUNT.
    """
    if line_width < 1:
        raise ValueError("line_width must be >= 1")
    if not 0 < parity_ratio < 1:
        raise ValueError("parity_ratio must be in (0, 1)")
    if nsym_line not in (0, 2, 4):
        raise ValueError("nsym_line must be 0, 2, or 4")
    bytes_per_line = _bytes_per_line(line_width, spec)
    if bytes_per_line < 1:
        raise ValueError("line_width too small to carry a byte")
    protected_len = _HEADER_LEN + data_len
    nsym = _select_nsym(protected_len, parity_ratio)
    nblocks = _num_blocks(protected_len, nsym)
    parity_len = nblocks * nsym
    data_lines = (protected_len + bytes_per_line - 1) // bytes_per_line
    parity_lines = (parity_len + bytes_per_line - 1) // bytes_per_line
    return bytes_per_line, protected_len, nsym, nblocks, data_lines, parity_lines


def encoded_line_count(
    data_len: int,
    *,
    line_width: int = 60,
    parity_ratio: float = 0.12,
    nsym_line: int = 2,
) -> int:
    """Return the exact number of lines without reading or encoding payload data.

    ``nsym_line`` (default 2, matching :meth:`Base16GCodec.encode`'s default)
    does not change the returned COUNT (see :func:`_encoding_shape`); it is
    accepted and validated here so callers pass the same value they encode
    with, and so an invalid ``nsym_line`` is caught at the same call site a
    caller would naturally check page planning from.
    """
    if data_len < 0 or data_len > 0xFFFFFFFF:
        raise ValueError("data length must fit the base16g-crc16-rs unsigned 32-bit header")
    return sum(_encoding_shape(data_len, line_width, parity_ratio, BASE16G, nsym_line)[-2:])


class StreamShape(_ty.NamedTuple):
    """Realized Reed-Solomon shape of an encoded ``L``/``P`` line stream.

    ``nsym``/``nblocks`` are ``None`` when the stream shape is ambiguous
    (``_candidate_nsym`` returned other than exactly one candidate) -- never
    guessed. ``nsym`` is the per-interleaved-block erasure budget. ``nsym_line``
    is detected STRUCTURALLY from the printed line shape (3 vs 4 tokens/line),
    the same heuristic decode itself uses before the group header is
    recoverable -- not read from the (possibly-undecoded) header.
    """

    data_lines: int
    parity_lines: int
    nsym: _ty.Optional[int]
    nblocks: _ty.Optional[int]
    data_bytes: int
    parity_bytes: int
    nsym_line: int = 0


def describe_line_stream(
    lines: _ty.Iterable[str], spec: "_RadixSpec" = BASE16G
) -> StreamShape:
    """Report the realized RS shape of an encoded line stream, read-only.

    Mirrors :meth:`Base16GCodec.decode_spool`'s modal-width bookkeeping (widest
    payload among non-last lines sets ``bytes_per_line``) to compute the data
    and parity byte totals, then derives ``nsym``/``nblocks`` from
    :func:`_candidate_nsym`/:func:`_num_blocks`. It never corrects, decodes, or
    writes anything -- it exists so callers (e.g. ``glyphive inspect``) can
    report a document's per-line redundancy without a full decode. If the
    stream cannot be interpreted (no data lines, or an ambiguous nsym), the
    RS fields are ``None`` rather than a guess.

    ``spec`` must be the radix spec of the codec that produced the stream
    (``Codec.get(name)._spec``); lines framed with a different alphabet or
    delimiter simply fail to parse and are not counted.
    """
    materialized = list(lines)
    line_parity_chars = _detect_line_parity_chars(materialized, spec)
    nsym_line = _nsym_line_for_chars(line_parity_chars, spec)
    data: _ty.Dict[int, "_ParsedLine"] = {}
    parity: _ty.Dict[int, "_ParsedLine"] = {}
    for raw in materialized:
        parsed = _parse_line(raw, spec, line_parity_chars=line_parity_chars)
        if parsed is None:
            continue
        (data if parsed.kind == "L" else parity)[parsed.idx] = parsed

    def _totals(index: _ty.Dict[int, "_ParsedLine"]) -> int:
        if not index:
            return 0
        max_idx = max(index)
        # Modal payload width among non-last lines sets the per-line byte span,
        # exactly as decode does; the last line may legitimately be shorter.
        widths = _collections.Counter(
            len(index[i].payload) for i in index if i != max_idx
        )
        modal = widths.most_common(1)[0][0] if widths else (
            len(index[max_idx].payload)
        )
        bytes_per_line = _bytes_per_line(modal, spec)
        if bytes_per_line < 1:
            return 0
        total = 0
        for i in range(max_idx + 1):
            entry = index.get(i)
            if entry is None:
                total += bytes_per_line
                continue
            total += _payload_byte_len(
                entry.payload, bytes_per_line, i == max_idx, spec
            )
        return total

    data_bytes = _totals(data)
    parity_bytes = _totals(parity)
    nsym: _ty.Optional[int] = None
    nblocks: _ty.Optional[int] = None
    if data:
        candidates = _candidate_nsym(data_bytes, parity_bytes)
        if len(candidates) == 1:
            nsym = candidates[0]
            nblocks = _num_blocks(data_bytes, nsym)
    return StreamShape(
        data_lines=len(data),
        parity_lines=len(parity),
        nsym=nsym,
        nblocks=nblocks,
        data_bytes=data_bytes,
        parity_bytes=parity_bytes,
        nsym_line=nsym_line,
    )


def _frame_stream(kind: str, source, length: int, bytes_per_line: int,
                  spec: "_RadixSpec" = BASE16G, *, nsym_line: int = 0):
    index = 0
    remaining = length
    while remaining:
        chunk = source.read(min(bytes_per_line, remaining))
        if not chunk:
            raise ValueError(f"truncated {kind} spool during framing")
        remaining -= len(chunk)
        if index >= spec.max_idx:
            raise ValueError(f"{kind} stream exceeds {spec.max_idx} lines")
        yield _frame(kind, index, chunk, spec, nsym_line=nsym_line)
        index += 1
    if source.read(1):
        raise ValueError(f"{kind} spool has trailing bytes")


def _preprocess_spool(
    source,
    sink,
    spec: "_RadixSpec",
    *,
    char_conf: _ty.Optional[
        _ty.Sequence[_ty.Optional[_ty.Sequence[float]]]
    ] = None,
) -> _ty.Tuple[bool, _ty.Optional[_ty.List[_ty.Optional[_ty.Sequence[float]]]]]:
    """Decode-hardening pre-pass (plans 1 & 2). Streams ``source`` -> ``sink``
    applying, to CRC-*failed* L/P lines only:

      1. in-line Reed-Solomon correction (:func:`line_rs_correct`) -- runs
         FIRST: a line carrying a non-empty line-parity field is
         blind-corrected and the fix accepted only if it reproduces the
         printed CRC, restoring true geometry without ever touching the
         document-level RS erasure budget;
      2. CRC-guided single-substitution repair (:func:`repair_line`) -- a
         repaired line re-enters as CRC-valid and restores true geometry;
      3. trusted-geometry filtering -- a still-failed line whose claimed index
         exceeds the max CRC-valid index of its kind is not allowed to extend the
         stream: it is positionally reassigned when it sits in a unit gap between
         two CRC-valid same-kind neighbors (still a CRC-failed erasure candidate),
         otherwise dropped (its true slot stays a missing-line erasure).

    ``line_parity_chars`` (the printed width of the optional line-parity
    field) is detected STRUCTURALLY once, up front, from the raw lines
    (:func:`_detect_line_parity_chars`) -- decode needs it before the group
    header (which carries the authoritative ``nsym_line``) can even be
    assembled, since it changes where the payload/check boundary falls. The
    header cross-check happens later in ``_decode_hardened_spool`` once the
    header itself is recovered; a mismatch there is a hard error.

    Clean and structurally-foreign lines pass through verbatim. Returns True if it
    changed anything (so the caller can keep the original spool on the fast path).
    Buffers only small per-line metadata (kind/idx/ok/text), never payloads-at-scale.
    """
    source.seek(0)
    raw_lines: _ty.List[str] = []
    while True:
        raw = source.readline()
        if not raw:
            break
        raw_lines.append(raw.decode("utf-8").rstrip("\r\n"))

    line_parity_chars = _detect_line_parity_chars(raw_lines, spec)
    nsym_line = _nsym_line_for_chars(line_parity_chars, spec)
    entries: _ty.List[_ty.Tuple[_ty.Optional[_ParsedLine], str]] = [
        (_parse_line(text, spec, line_parity_chars=line_parity_chars), text)
        for text in raw_lines
    ]
    changed = False

    # Tier 1: in-line RS correction, CRC re-verified. Runs first because a
    # successful in-line fix restores exact geometry without any of the
    # heuristics below (positional reassignment, ambiguity-driven refusal).
    if nsym_line:
        for i, (parsed, text) in enumerate(entries):
            if parsed is not None and not parsed.ok:
                fixed = line_rs_correct(text, spec, nsym_line=nsym_line)
                if fixed is not None:
                    reparsed = _parse_line(fixed, spec, line_parity_chars=line_parity_chars)
                    if reparsed is not None and reparsed.ok:
                        entries[i] = (reparsed, fixed)
                        changed = True

    # Tier 2: repair remaining CRC-failed lines (a repaired index restores
    # geometry before the trusted-geometry rule measures max_ok).
    for i, (parsed, text) in enumerate(entries):
        if parsed is not None and not parsed.ok:
            repaired = repair_line(text, spec, line_parity_chars=line_parity_chars)
            if repaired is not None:
                reparsed = _parse_line(repaired, spec, line_parity_chars=line_parity_chars)
                if reparsed is not None and reparsed.ok:
                    entries[i] = (reparsed, repaired)
                    changed = True

    # Max CRC-valid index per kind, computed from CRC-valid lines only.
    max_ok = {"L": -1, "P": -1}
    for parsed, _text in entries:
        if parsed is not None and parsed.ok:
            max_ok[parsed.kind] = max(max_ok[parsed.kind], parsed.idx)

    # ``out_conf[i]`` carries the ORIGINAL (raw, per-physical-input-line)
    # confidence for ``out[i]`` forward unchanged -- a positionally-reassigned
    # line's payload/line-parity/check characters are untouched (only the
    # index token's VALUE, never its WIDTH, changes), and a still-CRC-failed
    # line that survives verbatim obviously keeps its own confidence. A
    # dropped line's confidence is dropped with it. ``_assemble_to_spool``
    # re-validates the length against the actual on-disk text before trusting
    # it, so a case where reassignment changed the printed spacing (and thus
    # the char-for-char alignment) safely degrades to "no confidence" rather
    # than mis-aligning -- never a correctness risk, only a missed hint.
    conf_in = list(char_conf) if char_conf is not None else None
    out: _ty.List[str] = []
    out_conf: _ty.Optional[_ty.List[_ty.Optional[_ty.Sequence[float]]]] = (
        [] if conf_in is not None else None
    )

    def _emit(line: str, i: int) -> None:
        out.append(line)
        if out_conf is not None:
            out_conf.append(conf_in[i] if i < len(conf_in) else None)

    for i, (parsed, text) in enumerate(entries):
        if parsed is None or parsed.ok:
            _emit(text, i)
            continue
        # CRC-failed line still.
        if max_ok[parsed.kind] < 0 or parsed.idx <= max_ok[parsed.kind]:
            # Either the index is within the CRC-valid extent (plausible), or no
            # line of this kind passed CRC at all -- in the latter case there is
            # no trusted geometry to violate, so keep the line as an erasure
            # candidate rather than dropping it (dropping guarantees "no data
            # lines"; keeping lets RS/length bookkeeping still try).
            _emit(text, i)
            continue
        # Implausible index: try positional reassignment from CRC-valid neighbors.
        prev = nxt = None
        for j in range(i - 1, -1, -1):
            q = entries[j][0]
            if q is not None and q.ok and q.kind == parsed.kind:
                prev = q.idx
                break
        for j in range(i + 1, len(entries)):
            q = entries[j][0]
            if q is not None and q.ok and q.kind == parsed.kind:
                nxt = q.idx
                break
        if prev is not None and nxt is not None and nxt - prev == 2:
            fixed_idx = prev + 1
            token = _encode_index(fixed_idx, spec)
            split = split_frame_with_parity(
                text, spec=spec, line_parity_chars=line_parity_chars
            )
            if split is not None:
                _label, payload, line_parity, check = split
                # Keep payload/line-parity/check; CRC still fails -> stays an
                # erasure, but now in its true positional slot instead of a
                # poisoned index.
                parity_field = f" {line_parity}" if line_parity_chars else ""
                _emit(f"{parsed.kind}{token} {payload}{parity_field} {check}", i)
                changed = True
                continue
        # Otherwise drop the geometry-poisoning claim entirely.
        changed = True

    for line in out:
        sink.write(line.encode("utf-8") + b"\n")
    return changed, out_conf


def _read_spooled_line(
    source, offset: int, spec: "_RadixSpec" = BASE16G, *, line_parity_chars: int = 0
) -> _ParsedLine:
    source.seek(offset)
    parsed = _parse_line(
        source.readline().decode("utf-8").rstrip("\r\n"),
        spec,
        line_parity_chars=line_parity_chars,
    )
    if parsed is None:
        raise ValueError("indexed codec line is no longer parseable")
    return parsed


def _read_spooled_text(source, offset: int) -> str:
    """Re-read the exact stripped text of a spooled line (for conf alignment).

    Only called for the rare CRC-failed line that also has OCR confidence
    data -- an extra seek+read here is cheap relative to the RS work it may
    save.
    """
    source.seek(offset)
    return source.readline().decode("utf-8").rstrip("\r\n").strip()


def _assemble_to_spool(
    source,
    index,
    sink,
    bytes_per_line: int,
    spec: "_RadixSpec" = BASE16G,
    *,
    line_parity_chars: int = 0,
    line_conf: _ty.Optional[_ty.Mapping[int, _ty.Sequence[float]]] = None,
    conf_threshold: float = DEFAULT_CONF_THRESHOLD,
    max_suspects: int = DEFAULT_MAX_SUSPECTS,
):
    """Assemble one kind's (data or parity) byte stream, marking erasures.

    ``line_conf`` (optional): maps a line's spool file OFFSET (the same
    ``entry[0]`` this function reads lines at) to that line's RAW per-
    character OCR confidence (one value per PRINTED character of the line,
    spaces included -- see :func:`align_payload_char_conf`, which this calls
    to recover the PAYLOAD-aligned slice). For a CRC-failed line with
    confidence data, when the number of payload characters below
    ``conf_threshold`` is in ``1..max_suspects``, only the byte offsets those
    characters map to (:func:`_suspect_byte_offsets`) are added to
    ``erasures`` -- the line's other bytes are decoded and written through as
    ordinary ("soft", unverified) data instead of being zeroed. Any line that
    still falls back to whole-span erasure (no confidence, ambiguous
    alignment, too many/zero suspects, or an undecodable payload) behaves
    exactly as before.

    Returns ``(written, erasures, soft_spans)``: ``soft_spans`` is the list
    of ``(start, end)`` byte spans (within THIS stream) that got char-level
    marking instead of a whole-line erasure -- the caller's two-pass safety
    valve promotes a span back to full erasure, block-locally, if RS still
    fails to correct the block (see the module docstring's "OCR-confidence
    erasure hint" section).
    """
    erasures: _ty.List[int] = []
    soft_spans: _ty.List[_ty.Tuple[int, int]] = []
    if not index:
        return 0, erasures, soft_spans
    max_idx = max(index)
    written = 0
    for idx in range(max_idx + 1):
        entry = index.get(idx)
        parsed = (
            _read_spooled_line(source, entry[0], spec, line_parity_chars=line_parity_chars)
            if entry is not None
            else None
        )
        is_last = idx == max_idx
        if parsed is not None:
            span = _payload_byte_len(parsed.payload, bytes_per_line, is_last, spec)
        else:
            span = bytes_per_line
        # A line is usable only if it passes CRC on re-parse AND its stored
        # index entry was not already marked bad (entry[1] is False). The
        # stored flag is how the caller's modal-width check forces a
        # wrong-width line into an erasure even when its CRC coincidentally
        # passes -- without consulting it here, that force was inert.
        stored_ok = entry[1] if entry is not None else False
        if parsed is not None and parsed.ok and stored_ok:
            try:
                chunk = radix_decode(parsed.payload, span, spec)
            except ValueError:
                chunk = b"\x00" * span
                erasures.extend(range(written, written + span))
        else:
            chunk = None
            raw_conf = (
                line_conf.get(entry[0])
                if (line_conf and entry is not None)
                else None
            )
            if raw_conf is not None and parsed is not None:
                raw_text = _read_spooled_text(source, entry[0])
                payload_conf = align_payload_char_conf(
                    raw_text, raw_conf, spec, line_parity_chars=line_parity_chars
                )
                if payload_conf is not None and len(payload_conf) == len(parsed.payload):
                    suspects = [
                        i
                        for i, c in enumerate(payload_conf)
                        if c is not None and c < conf_threshold
                    ]
                    if 1 <= len(suspects) <= max_suspects:
                        try:
                            candidate = radix_decode(parsed.payload, span, spec)
                        except ValueError:
                            candidate = None
                        if candidate is not None:
                            suspect_bytes = _suspect_byte_offsets(suspects, span, spec)
                            if suspect_bytes:
                                chunk = candidate
                                erasures.extend(
                                    sorted(written + b for b in suspect_bytes)
                                )
                                soft_spans.append((written, written + span))
            if chunk is None:
                chunk = b"\x00" * span
                erasures.extend(range(written, written + span))
        sink.write(chunk)
        written += span
    return written, erasures, soft_spans


def _copy_stream(source, sink, chunk_size=1024 * 1024):
    source.seek(0)
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            return
        sink.write(chunk)


def _payload_byte_len(
    payload: str, bytes_per_line: int, is_last: bool, spec: "_RadixSpec" = BASE16G
) -> int:
    """Bytes carried by a payload: full lines carry ``bytes_per_line``; the last
    line carries what its (possibly shorter) width encodes."""
    if not is_last:
        return bytes_per_line
    if spec.packing == "group":
        # Whole groups plus the partial final group's byte count.
        n = len(payload)
        whole, rem_chars = divmod(n, spec.group_chars)
        total = whole * spec.group_bytes
        if rem_chars:
            import math
            # largest k bytes whose char count == rem_chars
            k = (rem_chars * math.log2(spec.radix)) // 8
            total += int(k)
        return total
    # Last line: derive byte count from its own char width (floor(bits*chars/8)).
    return (len(payload) * spec.bits) // 8


def _first_failed_label(
    data_lines: _ty.Dict[int, "_ParsedLine"],
    parity_lines: _ty.Dict[int, "_ParsedLine"],
) -> str:
    """Return the label of the first (lowest-index) CRC-failed or missing line."""
    for kind, parsed in (("L", data_lines), ("P", parity_lines)):
        if not parsed:
            continue
        for idx in range(max(parsed) + 1):
            line = parsed.get(idx)
            if line is None or not line.ok:
                return f"{kind}{idx:05d}"
    return "L00000"


class Base16GCodec(Codec):
    """The ``base16g-crc16-rs`` codec: 16-char OCR-safe alphabet / CRC-16-CCITT / Reed-Solomon.

    This is also the shared base for the denser radix codecs (``base8``/``base32g``/
    ``base64``): they subclass it, overriding only ``name`` and ``_spec``. All the
    RS/header/spool machinery is radix-agnostic and driven by ``self._spec``.
    """

    name = "base16g-crc16-rs"

    #: The radix parameters this codec frames with. Subclasses override this
    #: (and ``name``) to get a denser alphabet; everything else is inherited.
    _spec: _ty.ClassVar["_RadixSpec"] = BASE16G

    def encode(
        self,
        data: bytes,
        *,
        line_width: int = 60,
        parity_ratio: float = 0.12,
        nsym_line: int = 2,
    ) -> _ty.List[str]:
        """Encode bytes into OCR-safe framed data and parity lines.

        ``nsym_line`` (default 2; must be 0, 2, or 4) is the per-line Reed-
        Solomon parity budget: 0 disables the in-line correction tier (the
        line-parity field is omitted entirely), 2/4 add that many parity
        bytes to every printed line so many single/double-character OCR
        errors self-heal without ever touching the document-level RS erasure
        budget (see the module docstring's "Per-line Reed-Solomon" section).
        """
        if line_width < 1:
            raise ValueError("line_width must be >= 1")
        if not 0 < parity_ratio < 1:
            raise ValueError("parity_ratio must be in (0, 1)")
        if nsym_line not in (0, 2, 4):
            raise ValueError("nsym_line must be 0, 2, or 4")

        orig_len = len(data)
        protected_len = _HEADER_LEN + orig_len
        nsym = _select_nsym(protected_len, parity_ratio)
        header = _make_header(nsym, orig_len, nsym_line)
        stream = header + data
        data_bytes, parity_bytes, _nblocks = _rs_encode(stream, nsym)

        lines: _ty.List[str] = []
        lines.extend(
            _frame_bytes("L", data_bytes, line_width, self._spec, nsym_line=nsym_line)
        )
        lines.extend(
            _frame_bytes("P", parity_bytes, line_width, self._spec, nsym_line=nsym_line)
        )
        return lines

    def iter_encode(
        self,
        source: _ty.BinaryIO,
        data_len: int,
        *,
        line_width: int = 60,
        parity_ratio: float = 0.12,
        nsym_line: int = 2,
        temp_dir: _ty.Optional[str] = None,
    ) -> _ty.Iterator[str]:
        """Encode a seekable payload source with bounded Python allocations.

        The existing interleaved RS layout is preserved exactly (document-level
        data interleave), plus the v2 symbol-major PARITY interleave (see
        :func:`_parity_position`). A temporary parity spool avoids retaining
        parity or framed lines in memory: each block's parity is written
        contiguously (cheap sequential writes), then re-read through the
        interleaved position mapping when framing ``P`` lines -- the spool
        never exceeds ``nsym * nblocks`` bytes, which stays tiny (RS blocks
        are capped at 255 bytes each). mmap is used when the source exposes a
        file descriptor so each RS codeword is at most 255 bytes in Python
        memory. ``nsym_line`` (default 2) is documented on :meth:`encode`.
        """
        if data_len < 0 or data_len > 0xFFFFFFFF:
            raise ValueError("data length must fit the base16g-crc16-rs unsigned 32-bit header")
        if nsym_line not in (0, 2, 4):
            raise ValueError("nsym_line must be 0, 2, or 4")
        (
            bytes_per_line,
            protected_len,
            nsym,
            nblocks,
            _data_lines,
            _parity_lines,
        ) = _encoding_shape(data_len, line_width, parity_ratio, self._spec, nsym_line)
        header = _make_header(nsym, data_len, nsym_line)
        source.seek(0, 2)
        actual_len = source.tell()
        if actual_len != data_len:
            direction = "truncated" if actual_len < data_len else "grew"
            raise ValueError(
                f"source {direction} before encoding "
                f"(expected {data_len} bytes, found {actual_len})"
            )
        source.seek(0)
        mapping = None
        if data_len:
            try:
                mapping = _mmap.mmap(source.fileno(), 0, access=_mmap.ACCESS_READ)
            except (AttributeError, OSError, ValueError):
                mapping = None
        try:
            with _tempfile.TemporaryFile(dir=temp_dir) as block_parity_spool:
                rs = _reedsolo.RSCodec(nsym)
                for block_index in range(nblocks):
                    block = bytearray()
                    for position in range(block_index, protected_len, nblocks):
                        if position < _HEADER_LEN:
                            block.append(header[position])
                        elif mapping is not None:
                            block.append(mapping[position - _HEADER_LEN])
                        else:
                            source.seek(position - _HEADER_LEN)
                            value = source.read(1)
                            if not value:
                                raise ValueError("source was truncated during RS encoding")
                            block.extend(value)
                    encoded = rs.encode(bytes(block))
                    block_parity_spool.write(encoded[len(block):])

                source.seek(0)
                pending = bytearray(header)
                data_remaining = data_len
                line_index = 0
                while pending or data_remaining:
                    needed = bytes_per_line - len(pending)
                    if needed and data_remaining:
                        chunk = source.read(min(needed, data_remaining))
                        if not chunk:
                            raise ValueError("source was truncated during data framing")
                        pending.extend(chunk)
                        data_remaining -= len(chunk)
                    if len(pending) < bytes_per_line and data_remaining:
                        continue
                    chunk = bytes(pending[:bytes_per_line])
                    del pending[:bytes_per_line]
                    if line_index >= self._spec.max_idx:
                        raise ValueError(f"L stream exceeds {self._spec.max_idx} lines")
                    yield _frame(
                        "L", line_index, chunk, self._spec, nsym_line=nsym_line
                    )
                    line_index += 1
                if source.read(1):
                    raise ValueError("source grew during data framing")

                # Re-read the contiguously-written per-block parity through the
                # v2 interleaved (symbol-major) position mapping -- the spool
                # holds nsym*nblocks bytes total (always tiny; RS blocks are
                # capped at 255 bytes), so a full in-memory read is bounded.
                block_parity_spool.seek(0)
                contiguous_parity = block_parity_spool.read()
                interleaved_parity = bytearray(len(contiguous_parity))
                for b in range(nblocks):
                    for j in range(nsym):
                        interleaved_parity[_parity_position(j, b, nblocks)] = (
                            contiguous_parity[b * nsym + j]
                        )
                with _io.BytesIO(bytes(interleaved_parity)) as parity:
                    yield from _frame_stream(
                        "P",
                        parity,
                        nblocks * nsym,
                        bytes_per_line,
                        self._spec,
                        nsym_line=nsym_line,
                    )
        finally:
            if mapping is not None:
                mapping.close()

    def decode(self, lines: _ty.Iterable[str]) -> bytes:
        """Decode framed lines using only CRC and RS as correctness oracles."""
        encoded = _io.BytesIO()
        for line in lines:
            encoded.write(line.encode("utf-8") + b"\n")
        encoded.seek(0)
        output = _io.BytesIO()
        self.decode_spool(encoded, output)
        return output.getvalue()

    def decode_spool(
        self,
        encoded_source: _ty.BinaryIO,
        payload_sink: _ty.BinaryIO,
        *,
        char_conf: _ty.Optional[
            _ty.Sequence[_ty.Optional[_ty.Sequence[float]]]
        ] = None,
        conf_threshold: float = DEFAULT_CONF_THRESHOLD,
        max_suspects: int = DEFAULT_MAX_SUSPECTS,
        temp_dir: _ty.Optional[str] = None,
    ) -> None:
        """Decode an encoded-line spool with only offsets and RS blocks in RAM.

        ``char_conf`` (plan 3, optional): per-line OCR character confidence,
        keyed by PHYSICAL LINE ORDER within ``encoded_source`` (``char_conf[i]``
        is the raw per-character confidence of the i-th line as read from the
        spool, or ``None``) -- never by printed index, which may itself be
        corrupt on a CRC-failed line. When absent (the default), decode is
        byte-identical to a build without this feature. See the module
        docstring's "OCR-confidence erasure hint" section for exactly what
        this does and does not change about acceptance.
        """
        # Plan-1 decode-hardening pre-pass: repair single-char errors, and stop
        # CRC-failed lines with poisoned index tokens from destroying the stream
        # geometry. Runs into a temp spool only when it actually changes lines;
        # a clean transcript pays one streaming scan and reuses the original.
        # ``char_conf`` (raw, per-physical-line) rides along unchanged for kept
        # lines and is dropped along with any line the hardening pass drops --
        # see ``_preprocess_spool``.
        with _tempfile.TemporaryFile(dir=temp_dir) as _hardened:
            changed, hardened_conf = _preprocess_spool(
                encoded_source, _hardened, self._spec, char_conf=char_conf
            )
            if changed:
                _hardened.seek(0)
                self._decode_hardened_spool(
                    _hardened,
                    payload_sink,
                    temp_dir=temp_dir,
                    char_conf=hardened_conf,
                    conf_threshold=conf_threshold,
                    max_suspects=max_suspects,
                )
                return
        self._decode_hardened_spool(
            encoded_source,
            payload_sink,
            temp_dir=temp_dir,
            char_conf=char_conf,
            conf_threshold=conf_threshold,
            max_suspects=max_suspects,
        )

    def _decode_hardened_spool(
        self,
        encoded_source: _ty.BinaryIO,
        payload_sink: _ty.BinaryIO,
        *,
        char_conf: _ty.Optional[
            _ty.Sequence[_ty.Optional[_ty.Sequence[float]]]
        ] = None,
        conf_threshold: float = DEFAULT_CONF_THRESHOLD,
        max_suspects: int = DEFAULT_MAX_SUSPECTS,
        temp_dir: _ty.Optional[str] = None,
    ) -> None:
        data_lines: _ty.Dict[int, _ty.Tuple[int, bool, int]] = {}
        parity_lines: _ty.Dict[int, _ty.Tuple[int, bool, int]] = {}
        # Per-index payload seen for a CRC-passing line, to detect a *conflicting*
        # collision (finding #3): a corrupted label that decodes to a real but
        # wrong index would silently overwrite a different genuine line under
        # blind last-write-wins. Track (payload) so we can distinguish a benign
        # duplicate (same bytes re-read) from a true conflict.
        ok_payload: _ty.Dict[_ty.Tuple[str, int], str] = {}
        collisions: _ty.List[str] = []
        collided: _ty.Set[_ty.Tuple[str, int]] = set()
        length_counts: _ty.Counter[int] = _collections.Counter()
        # The optional line-parity field's printed width is not known until the
        # group header is recovered (it carries the authoritative nsym_line),
        # but the field's presence changes where payload/check fall on EVERY
        # line -- so it is detected STRUCTURALLY up front (3 vs 4
        # whitespace-delimited tokens per line) and later cross-checked against
        # the header once that's recoverable; a mismatch is a hard error.
        encoded_source.seek(0)
        line_parity_chars = _detect_line_parity_chars(
            (raw.decode("utf-8", "replace") for raw in encoded_source), self._spec
        )
        encoded_source.seek(0)
        # ``char_conf`` is indexed by PHYSICAL LINE ORDER (see decode_spool's
        # docstring) -- resolve it here, once, into an offset-keyed lookup so
        # ``_assemble_to_spool`` can find a line's confidence the same way it
        # already finds everything else about that line: by spool offset.
        line_conf_by_offset: _ty.Dict[int, _ty.Sequence[float]] = {}
        phys_index = 0
        while True:
            offset = encoded_source.tell()
            raw = encoded_source.readline()
            if not raw:
                break
            if (
                char_conf is not None
                and phys_index < len(char_conf)
                and char_conf[phys_index] is not None
            ):
                line_conf_by_offset[offset] = char_conf[phys_index]
            phys_index += 1
            parsed = _parse_line(
                raw.decode("utf-8").rstrip("\r\n"),
                self._spec,
                line_parity_chars=line_parity_chars,
            )
            if parsed is None:
                continue
            target = data_lines if parsed.kind == "L" else parity_lines
            if parsed.ok:
                key = (parsed.kind, parsed.idx)
                prior = ok_payload.get(key)
                if prior is not None and prior != parsed.payload:
                    # Two CRC-valid lines claim the same index with different
                    # payloads (a corrupted label landing on a real index with a
                    # spurious CRC match, or an L<->P kind-flip). Degrade to an
                    # erasure (drop both) instead of aborting -- RS very likely
                    # rebuilds that slot, and the SHA-256 gate backstops it. Only
                    # if the resulting erasure load then exceeds the RS budget does
                    # decode fail, via the existing budget-exceeded path.
                    collisions.append(f"{parsed.kind}{parsed.idx:05d}")
                    collided.add(key)
                else:
                    ok_payload[key] = parsed.payload
            existing = target.get(parsed.idx)
            # Prefer a CRC-passing line over a CRC-failing one for the same index
            # instead of blind last-write-wins; only overwrite if the new line is
            # at least as trustworthy (ok) as what's already there.
            if existing is None or parsed.ok or not existing[1]:
                target[parsed.idx] = (offset, parsed.ok, len(parsed.payload))
            length_counts[len(parsed.payload)] += 1
        # Fix 3: force every collided index into an erasure (drop both payloads)
        # instead of raising. The index becomes a missing-line erasure that RS can
        # rebuild within budget; if it cannot, the budget-exceeded path reports it.
        for kind, idx in collided:
            target = data_lines if kind == "L" else parity_lines
            entry = target.get(idx)
            if entry is not None:
                target[idx] = (entry[0], False, entry[2])

        if not data_lines:
            raise ValueError("no data lines found to decode")
        # Derive the line width from the MODAL payload length, not max(): a single
        # OCR-corrupted line whose length is off by a couple of characters must not
        # widen bytes_per_line for every other, perfectly good line on the stream
        # (real-recovery finding #4 — 3 bad lines out of ~3500 broke an otherwise
        # successful 72-page RS decode). Any non-last line whose length disagrees
        # with the modal width is itself evidence of corruption and is forced into
        # an erasure below, exactly as a CRC failure would be. The last line of each
        # kind is legitimately shorter, so it never votes on or is judged by the
        # modal width.
        data_last = max(data_lines)
        parity_last = max(parity_lines) if parity_lines else None
        modal_counts: _ty.Counter[int] = _collections.Counter()
        for kind_index, last_idx in ((data_lines, data_last), (parity_lines, parity_last)):
            for idx, (_offset, _ok, payload_len) in kind_index.items():
                if idx != last_idx:
                    modal_counts[payload_len] += 1
        if modal_counts:
            modal_payload = modal_counts.most_common(1)[0][0]
        else:
            # Only last lines present (a tiny 1-line-per-kind stream): fall back to
            # the largest observed length, which is the single real line's width.
            modal_payload = max(length_counts)
        # Force any non-last line whose width != modal into an erasure (ok=False)
        # so a wrong-length line is corrected by RS instead of corrupting geometry.
        for kind_index, last_idx in ((data_lines, data_last), (parity_lines, parity_last)):
            for idx, (line_offset, ok, payload_len) in list(kind_index.items()):
                if idx != last_idx and payload_len != modal_payload:
                    kind_index[idx] = (line_offset, False, payload_len)
        bytes_per_line = _bytes_per_line(modal_payload, self._spec)
        with _tempfile.TemporaryFile(dir=temp_dir) as data_spool, _tempfile.TemporaryFile(
            dir=temp_dir
        ) as parity_spool:
            data_len, data_erasures, data_soft_spans = _assemble_to_spool(
                encoded_source,
                data_lines,
                data_spool,
                bytes_per_line,
                self._spec,
                line_parity_chars=line_parity_chars,
                line_conf=line_conf_by_offset or None,
                conf_threshold=conf_threshold,
                max_suspects=max_suspects,
            )
            parity_len, parity_erasures, parity_soft_spans = _assemble_to_spool(
                encoded_source,
                parity_lines,
                parity_spool,
                bytes_per_line,
                self._spec,
                line_parity_chars=line_parity_chars,
                line_conf=line_conf_by_offset or None,
                conf_threshold=conf_threshold,
                max_suspects=max_suspects,
            )
            if data_len < _HEADER_LEN:
                raise ValueError("data stream shorter than the group header")
            candidates = _candidate_nsym(data_len, parity_len)
            if not candidates:
                raise ValueError(
                    "cannot recover RS parameters: data/parity line counts are "
                    "inconsistent (missing or spurious lines)"
                )
            data_erasure_set = set(data_erasures)
            parity_erasure_set = set(parity_erasures)

            # Fast path: a stream with ZERO erasures is already fully trusted by
            # the per-line CRC oracle (every byte came from a CRC-matching line),
            # so Reed-Solomon has nothing to correct -- skip it entirely and
            # stream the payload straight out. RS's only remaining service on a
            # clean block is *blind* correction of an astronomically-rare CRC
            # false positive (~1/65536 per corrupted line); dropping that bonus
            # is safe because the whole-document SHA-256 gate (restore/decode.py)
            # still converts any residual corruption into a LOUD failure. Only
            # taken when the stream shape is unambiguous and consistent.
            if (
                not data_erasures
                and not parity_erasures
                and len(candidates) == 1
            ):
                only_nsym = candidates[0]
                if _num_blocks(data_len, only_nsym) * only_nsym == parity_len:
                    data_spool.seek(0)
                    prefix = data_spool.read(_HEADER_LEN)
                    try:
                        hdr_nsym, orig_len, hdr_nsym_line = _parse_header(prefix)
                    except ValueError:
                        hdr_nsym, orig_len, hdr_nsym_line = None, None, None
                    if hdr_nsym == only_nsym and hdr_nsym_line is not None:
                        expected_nsym_line = _nsym_line_for_chars(
                            line_parity_chars, self._spec
                        )
                        if hdr_nsym_line != expected_nsym_line:
                            raise ValueError(
                                "group header nsym_line "
                                f"({hdr_nsym_line}) disagrees with the printed "
                                f"line-parity field shape ({expected_nsym_line}) "
                                "-- corrupt or mismatched-version transcript"
                            )
                    if (
                        hdr_nsym == only_nsym
                        and orig_len is not None
                        and orig_len <= data_len - _HEADER_LEN
                    ):
                        remaining = orig_len
                        while remaining:
                            chunk = data_spool.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError(
                                    "clean payload spool is truncated"
                                )
                            payload_sink.write(chunk)
                            remaining -= len(chunk)
                        return
                    # Header didn't validate cleanly (should not happen for a
                    # zero-erasure stream); fall through to the RS path, which
                    # will either recover or raise the appropriate named error.

            data_spool.flush()
            parity_spool.flush()
            data_map = _mmap.mmap(data_spool.fileno(), 0, access=_mmap.ACCESS_READ)
            parity_map = _mmap.mmap(parity_spool.fileno(), 0, access=_mmap.ACCESS_READ)
            try:
                rs_budget_exceeded = False
                for nsym in candidates:
                    nblocks = _num_blocks(data_len, nsym)
                    expected_parity_len = nblocks * nsym
                    # Two-pass safety valve bookkeeping (plan 3): which
                    # interleaved block(s) each char-level-marked ("soft") line
                    # span touches, FOR THIS nblocks -- a block whose erasures
                    # are all char-level and still fails RS is retried once
                    # with every byte of the soft line(s) it touches erased
                    # across their full span (today's behaviour), before the
                    # block is given up as uncorrectable. Cheap: proportional
                    # to total soft bytes, which is bounded by the (small)
                    # number of CRC-failed lines with usable confidence.
                    soft_by_block_data: _ty.Dict[int, _ty.Set[int]] = (
                        _collections.defaultdict(set)
                    )
                    for start, end in data_soft_spans:
                        for pos in range(start, end):
                            soft_by_block_data[pos % nblocks].add(pos)
                    soft_by_block_parity: _ty.Dict[int, _ty.Set[int]] = (
                        _collections.defaultdict(set)
                    )
                    for start, end in parity_soft_spans:
                        for pos in range(start, end):
                            soft_by_block_parity[pos % nblocks].add(pos)
                    with _tempfile.TemporaryFile(dir=temp_dir) as corrected:
                        _copy_stream(data_spool, corrected)
                        rs = _reedsolo.RSCodec(nsym)
                        failed = False
                        for block_index in range(nblocks):
                            positions = list(range(block_index, data_len, nblocks))
                            block_data = bytes(data_map[pos] for pos in positions)
                            # v2: parity byte j of this block lives at the
                            # INTERLEAVED (symbol-major) offset j*nblocks+b,
                            # not the contiguous b*nsym+j -- see
                            # _parity_position.
                            parity_pos = [
                                _parity_position(j, block_index, nblocks)
                                for j in range(nsym)
                            ]
                            block_parity = bytes(
                                parity_map[pos] if pos < parity_len else 0
                                for pos in parity_pos
                            )
                            erase_pos = [
                                local for local, pos in enumerate(positions)
                                if pos in data_erasure_set
                            ]
                            erase_pos.extend(
                                len(positions) + offset
                                for offset, pos in enumerate(parity_pos)
                                if pos in parity_erasure_set or pos >= parity_len
                            )
                            # Per-block fast path: a block with no erasures is
                            # already CRC-trusted and its data bytes are already
                            # in ``corrected`` (a copy of data_spool), so skip
                            # the syndrome/RS work for it. Only blocks that
                            # actually carry an erasure need rs.decode.
                            if not erase_pos:
                                continue
                            try:
                                decoded = rs.decode(
                                    bytearray(block_data + block_parity),
                                    erase_pos=erase_pos or None,
                                )[0]
                            except _reedsolo.ReedSolomonError:
                                # Pass 2 of the safety valve: promote any soft
                                # line touching this block to a full-span
                                # erasure and retry ONCE, block-locally.
                                retry_pos = set(erase_pos)
                                soft_data_here = soft_by_block_data.get(block_index)
                                if soft_data_here:
                                    retry_pos.update(
                                        (pos - block_index) // nblocks
                                        for pos in soft_data_here
                                        if pos not in data_erasure_set
                                    )
                                soft_parity_here = soft_by_block_parity.get(block_index)
                                if soft_parity_here:
                                    retry_pos.update(
                                        len(positions) + (pos - block_index) // nblocks
                                        for pos in soft_parity_here
                                        if pos not in parity_erasure_set
                                    )
                                rescued = False
                                if len(retry_pos) > len(erase_pos):
                                    try:
                                        decoded = rs.decode(
                                            bytearray(block_data + block_parity),
                                            erase_pos=sorted(retry_pos),
                                        )[0]
                                        rescued = True
                                    except _reedsolo.ReedSolomonError:
                                        rescued = False
                                if not rescued:
                                    rs_budget_exceeded = True
                                    failed = True
                                    break
                            for local, pos in enumerate(positions):
                                if decoded[local] != data_map[pos]:
                                    corrected.seek(pos)
                                    corrected.write(bytes([decoded[local]]))
                        if failed:
                            continue
                        corrected.seek(0)
                        prefix = corrected.read(_HEADER_LEN)
                        try:
                            hdr_nsym, orig_len, hdr_nsym_line = _parse_header(prefix)
                        except ValueError:
                            continue
                        if hdr_nsym != nsym or orig_len > data_len - _HEADER_LEN:
                            continue
                        expected_nsym_line = _nsym_line_for_chars(
                            line_parity_chars, self._spec
                        )
                        if hdr_nsym_line != expected_nsym_line:
                            raise ValueError(
                                "group header nsym_line "
                                f"({hdr_nsym_line}) disagrees with the printed "
                                f"line-parity field shape ({expected_nsym_line}) "
                                "-- corrupt or mismatched-version transcript"
                            )
                        remaining = orig_len
                        while remaining:
                            chunk = corrected.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("corrected payload spool is truncated")
                            payload_sink.write(chunk)
                            remaining -= len(chunk)
                        return
            finally:
                data_map.close()
                parity_map.close()

        if rs_budget_exceeded:
            for kind, index in (("L", data_lines), ("P", parity_lines)):
                if index:
                    for idx in range(max(index) + 1):
                        entry = index.get(idx)
                        if entry is None or not entry[1]:
                            raise CodecError(
                                f"line {kind}{idx:05d} failed CRC and exceeds RS correction budget"
                            )
            raise CodecError("line L00000 failed CRC and exceeds RS correction budget")
        raise ValueError("decode failed: corrected stream did not yield a valid group header")
