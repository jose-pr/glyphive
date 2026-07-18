"""glyphive codec ``base16c-crc16-rs`` — byte stream ↔ OCR-safe printable text lines.

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

    <kind><idx> <payload> #<check>

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
- ``#``        : a literal ``#`` marking the start of the check field.
- ``<check>``  : ``CHECK_WIDTH`` (4) alphabet characters (16 bits) encoding a
                 16-bit CRC-16/CCITT (poly 0x1021, init 0xFFFF) computed over
                 the bytes ``idx_token.encode() + payload.encode()`` — i.e.
                 over the *printed* index token and the *printed* payload
                 characters, so a human can recompute it from the page by hand.

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
The very first bytes of a group's payload carry an 8-byte binary header so decode
can reconstruct the exact original byte length (nibble bit-packing pads the
final 4-bit group, so the raw length must be carried, never guessed):

    b"B1" | version:u8 | nsym:u8 | orig_len:u32-big-endian

The header bytes are part of the RS-protected data stream, so they are covered by
parity and per-line CRC just like the payload.

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

Decode oracle discipline (CRITICAL)
-----------------------------------
Correctness is judged SOLELY by the per-line CRC check and RS correction. There
is deliberately no "try confusable substitutions and keep whatever decompresses
further" search anywhere in this module. If a line's CRC fails AND RS cannot
correct the resulting erasures, ``decode`` RAISES a clear exception naming the
exact failing line label. It never guesses or mutates data to make it "work".
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
    "Base16CCodec",
    "nibble_encode",
    "nibble_decode",
    "encoded_line_count",
    "describe_line_stream",
    "StreamShape",
]

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
# denser codecs (base32/base64) are other specs (see codec/radix.py).
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
    """Immutable per-codec parameters derived from the alphabet + bits/char.

    ``bits`` is bits per printed character (``log2(len(alphabet))``, must be an
    integer power-of-two alphabet). ``check_width`` = chars to hold the 16-bit
    CRC = ``ceil(16 / bits)``. ``index_width`` is chosen per spec so index
    capacity (``radix ** index_width``) stays ~1e6+ lines. ``index_mask`` is a
    tuple of ``index_width`` distinct values in ``[0, radix)`` XORed per position
    to defeat OCR phantom-insertion into uniform runs.
    """

    __slots__ = (
        "name", "alphabet", "bits", "mask", "check_width", "index_width",
        "index_mask", "decode_map", "max_idx", "case_fold",
    )

    def __init__(self, name, alphabet, bits, check_width, index_width, index_mask):
        radix = len(alphabet)
        if radix != (1 << bits):
            raise ValueError(
                f"{name}: alphabet length {radix} != 2**{bits}"
            )
        if len(index_mask) != index_width:
            raise ValueError(f"{name}: index_mask needs {index_width} values")
        if any(not (0 <= v < radix) for v in index_mask):
            raise ValueError(f"{name}: index_mask values must be in [0,{radix})")
        self.name = name
        self.alphabet = alphabet
        self.bits = bits
        self.mask = (1 << bits) - 1
        self.check_width = check_width
        self.index_width = index_width
        self.index_mask = tuple(index_mask)
        self.decode_map = _build_decode_map(alphabet)
        self.max_idx = radix ** index_width
        # Case-folding OCR drift (.upper()) is only valid for a single-case
        # alphabet; a case-significant one (base64) must compare verbatim.
        _letters = [c for c in alphabet if c.isalpha()]
        self.case_fold = len({c.lower() for c in _letters}) == len(_letters)


#: The shipped base16c spec. Its constants reproduce the historical values
#: exactly, so every base16c-bound wrapper below is byte-for-byte unchanged.
BASE16C: _ty.Final["_RadixSpec"] = _RadixSpec(
    name="base16c-crc16-rs",
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


def radix_encode(data: bytes, spec: "_RadixSpec" = BASE16C) -> str:
    """Encode raw bytes to an alphabet string, ``spec.bits`` bits per char.

    MSB-first; the final group is zero-padded. This is *not* self-delimiting:
    the caller must track the original byte length to strip pad bits on decode
    (:func:`radix_decode` takes that length explicitly).
    """
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


def radix_decode(text: str, byte_len: int, spec: "_RadixSpec" = BASE16C) -> bytes:
    """Decode an alphabet string back to exactly ``byte_len`` bytes.

    Case-insensitive. No confusable aliases are applied (see the comment above
    ``_DECODE_MAP``): any character outside the alphabet raises ``ValueError``
    rather than being guessed at. Trailing pad bits beyond ``byte_len`` bytes
    are discarded.
    """
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
    return radix_encode(data, BASE16C)


def nibble_decode(text: str, byte_len: int) -> bytes:
    """base16c-bound :func:`radix_decode` (4 bits/char). Public API."""
    return radix_decode(text, byte_len, BASE16C)


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


def _check_chars(idx_token: str, payload: str, spec: "_RadixSpec" = BASE16C) -> str:
    """Compute the check field (``spec.check_width`` chars) for a framed line.

    The CRC covers exactly what is *printed* -- the index token and the payload
    characters -- so a human can recompute it straight off the page. The 16-bit
    CRC is rendered MSB-first across ``check_width`` chars of ``spec.bits`` bits;
    when ``check_width * bits > 16`` (e.g. base8), the top pad bits are zero.
    """
    crc = _crc16_ccitt(idx_token.encode() + payload.encode())
    bits = spec.bits
    mask = spec.mask
    alphabet = spec.alphabet
    chars = []
    for shift in range(spec.check_width - 1, -1, -1):
        chars.append(alphabet[(crc >> (bits * shift)) & mask])
    return "".join(chars)


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
    """
    bits = spec.bits
    mask = spec.mask
    alphabet = spec.alphabet
    width = spec.index_width
    imask = spec.index_mask
    return "".join(
        alphabet[((idx >> (bits * shift)) & mask) ^ imask[width - 1 - shift]]
        for shift in range(width - 1, -1, -1)
    )


def _decode_index(token: str, spec: "_RadixSpec") -> _ty.Optional[int]:
    """Inverse of :func:`_encode_index` (per spec); ``None`` if unreadable.

    Case-insensitive; no confusable aliases (see ``_DECODE_MAP``). A wrong
    result cannot slip through: the per-line CRC covers the printed token.
    """
    if len(token) != spec.index_width:
        return None
    bits = spec.bits
    decode_map = spec.decode_map
    imask = spec.index_mask
    value = 0
    for position, char in enumerate(token):
        try:
            digit = decode_map[char]
        except KeyError:
            return None
        value = (value << bits) | (digit ^ imask[position])
    return value


def encode_index(idx: int) -> str:
    """base16c-bound :func:`_encode_index`. Public API.

    ``0`` -> ``MYCVH``, ``1`` -> ``MYCVK``, ``1048575`` -> ``PCYHV``.
    """
    return _encode_index(idx, BASE16C)


def decode_index(token: str) -> _ty.Optional[int]:
    """base16c-bound :func:`_decode_index`. Public API."""
    return _decode_index(token, BASE16C)


def _frame(kind: str, idx: int, payload: str, spec: "_RadixSpec" = BASE16C) -> str:
    token = _encode_index(idx, spec)
    return f"{kind}{token} {payload} #{_check_chars(token, payload, spec)}"


class _ParsedLine(_ty.NamedTuple):
    kind: str  # "L" or "P"
    idx: int
    payload: str
    ok: bool  # True iff the CRC check field matched


def split_frame(
    line: str, *, allow_trailing: bool = False, spec: "_RadixSpec" = BASE16C
) -> _ty.Optional[_ty.Tuple[str, str, str]]:
    """Structurally split a printed line into ``(label, payload, check)``.

    OCR (observed: Tesseract) sometimes inserts a spurious space *inside* the
    payload (e.g. ``...FYWZQH4 6F1IWO0C...``), which would turn the intended 3
    whitespace tokens into 4+ and cause a naive ``line.split()`` shape test to
    discard an otherwise-perfect line. The frame's actual shape is not "exactly
    3 tokens" -- it is "a label, then a payload, then a ``#check`` field": the
    label is always the *first* token and ``#check`` is always the *last*
    token, so the payload is unambiguously everything in between.

    This is deterministic normalization, not guessing: the payload alphabet
    contains no whitespace, so any interior space is provably OCR noise, and
    joining the middle tokens with no separator recovers exactly the printed
    payload characters. The per-line CRC (computed downstream over this
    recovered payload) is what actually decides correctness -- this function
    only ensures a noisy-but-readable line reaches that check instead of being
    silently dropped before it gets the chance.

    Returns ``None`` if the line has fewer than 3 tokens or the last token
    does not start with ``#`` (i.e. it does not have the frame shape at all --
    e.g. the ``PAGE 1/1 sha256=...`` footer, which is 3 tokens but whose last
    token is not a check field).
    """
    parts = line.split()
    if len(parts) >= 3:
        check_positions = [
            index for index, part in enumerate(parts[1:], 1)
            if part.startswith("#")
        ]
        if len(check_positions) == 1:
            position = check_positions[0]
            if allow_trailing or position == len(parts) - 1:
                return parts[0], "".join(parts[1:position]), parts[position]

    # A constrained Tesseract whitelist can remove both printed separator
    # spaces while preserving every protected glyph.  The label and check have
    # fixed widths, and '#' is outside the payload alphabet, so this compact
    # shape remains unambiguous and still flows through the ordinary CRC check.
    stripped = line.strip()
    label_width = spec.index_width + 1
    if len(stripped) < label_width + 1 + spec.check_width:
        return None
    label = stripped[:label_width]
    remainder = stripped[label_width:]
    if remainder.count("#") != 1:
        return None
    marker = remainder.index("#")
    check_end = marker + 1 + spec.check_width
    if marker == 0 or check_end > len(remainder):
        return None
    trailing = remainder[check_end:]
    if trailing and not allow_trailing:
        return None
    payload = "".join(remainder[:marker].split())
    check = remainder[marker:check_end]
    return label, payload, check


def _parse_line(line: str, spec: "_RadixSpec" = BASE16C) -> _ty.Optional[_ParsedLine]:
    """Parse one framed line. Returns None for blank/foreign lines.

    ``ok`` reflects whether the printed check field matches a freshly computed
    CRC over the (alias-normalized) index+payload. A structurally broken line
    (missing fields, bad kind) is treated as a failed check on a best-effort
    index so decode can still localize it; if even the index is unreadable the
    line is skipped (its absence surfaces later as a length/erasure error).

    The line is split via :func:`split_frame`, which anchors the label as the
    first token and ``#check`` as the last, joining everything between as the
    payload -- this tolerates OCR-inserted interior spaces (see its docstring).
    """
    stripped = line.strip()
    if not stripped:
        return None
    split = split_frame(stripped, spec=spec)
    if split is None:
        return None
    label, payload, check = split
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
    expected = _check_chars(token_for_crc, payload, spec)
    ok = (check_chars.upper() if fold else check_chars) == expected
    return _ParsedLine(kind=kind, idx=idx, payload=payload, ok=ok)


# ---------------------------------------------------------------------------
# Group header (carries exact original length + RS params)
# ---------------------------------------------------------------------------

_MAGIC: _ty.Final[bytes] = b"B1"
_VERSION: _ty.Final[int] = 1
_HEADER_LEN: _ty.Final[int] = len(_MAGIC) + 1 + 1 + 4  # magic + ver + nsym + u32


def _make_header(nsym: int, orig_len: int) -> bytes:
    return _MAGIC + bytes([_VERSION, nsym]) + orig_len.to_bytes(4, "big")


def _parse_header(data: bytes) -> _ty.Tuple[int, int]:
    if len(data) < _HEADER_LEN or data[:2] != _MAGIC:
        raise ValueError("corrupt group header: bad magic or truncated stream")
    if data[2] != _VERSION:
        raise ValueError(f"unsupported codec version {data[2]}")
    nsym = data[3]
    orig_len = int.from_bytes(data[4:8], "big")
    return nsym, orig_len


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


def _rs_encode(data: bytes, nsym: int) -> _ty.Tuple[bytes, bytes, int]:
    """Return (data_bytes, parity_bytes, num_blocks).

    ``parity_bytes`` is the concatenation of each block's ``nsym`` parity
    symbols, block 0 first. ``data_bytes`` is returned unchanged (it is the
    stream the caller frames into ``L`` lines).
    """
    nblocks = _num_blocks(len(data), nsym)
    codec = _reedsolo.RSCodec(nsym)
    parity = bytearray()
    for b in range(nblocks):
        block = bytes(data[b::nblocks])  # interleaved stripe
        encoded = codec.encode(block)  # data + nsym parity
        parity.extend(encoded[len(block):])
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
    line failed CRC — RS treats them as known-position erasures. Raises
    ``_reedsolo.ReedSolomonError`` if a block exceeds the correction budget.
    """
    codec = _reedsolo.RSCodec(nsym)
    data_len = len(data)
    data_erasure_set = set(data_erasures)
    parity_erasure_set = set(parity_erasures)
    out = bytearray(data)
    for b in range(nblocks):
        data_pos = list(range(b, data_len, nblocks))  # global positions in stripe
        block_data = bytes(data[b::nblocks])
        block_parity = bytes(parity[b * nsym:(b + 1) * nsym])
        codeword = bytearray(block_data + block_parity)
        # Map global erasure positions to positions within this codeword.
        erase_pos: _ty.List[int] = []
        for local_i, gpos in enumerate(data_pos):
            if gpos in data_erasure_set:
                erase_pos.append(local_i)
        base = len(block_data)
        for j in range(nsym):
            if (b * nsym + j) in parity_erasure_set:
                erase_pos.append(base + j)
        decoded = codec.decode(codeword, erase_pos=erase_pos or None)
        corrected = decoded[0]  # data portion only (nsym stripped)
        for local_i, gpos in enumerate(data_pos):
            out[gpos] = corrected[local_i]
    return bytes(out[:data_len])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _frame_bytes(
    kind: str, data: bytes, line_width: int, spec: "_RadixSpec" = BASE16C
) -> _ty.List[str]:
    """Encode ``data`` with the alphabet and split into framed lines of ``line_width``.

    Each line's payload maps back to a whole number of bytes: we chunk the byte
    stream so that ``line_width`` alphabet chars carry a fixed byte count where
    possible, and the final line carries whatever remains. To keep byte↔line
    mapping exact and simple, we pack a fixed number of bytes per line such that
    they encode to at most ``line_width`` chars.
    """
    if not data:
        return []
    # bytes per full line: the largest B with ceil(8B/spec.bits) <= line_width.
    bytes_per_line = (line_width * spec.bits) // 8
    if bytes_per_line < 1:
        raise ValueError("line_width too small to carry a byte")
    lines: _ty.List[str] = []
    idx = 0
    for start in range(0, len(data), bytes_per_line):
        chunk = data[start:start + bytes_per_line]
        payload = radix_encode(chunk, spec)
        if idx >= spec.max_idx:
            raise ValueError(
                f"{kind} stream exceeds {spec.max_idx} lines; use smaller pages"
            )
        lines.append(_frame(kind, idx, payload, spec))
        idx += 1
    return lines


def _encoding_shape(
    data_len: int, line_width: int, parity_ratio: float, spec: "_RadixSpec" = BASE16C
):
    if line_width < 1:
        raise ValueError("line_width must be >= 1")
    if not 0 < parity_ratio < 1:
        raise ValueError("parity_ratio must be in (0, 1)")
    bytes_per_line = (line_width * spec.bits) // 8
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
    data_len: int, *, line_width: int = 60, parity_ratio: float = 0.12
) -> int:
    """Return the exact number of lines without reading or encoding payload data."""
    if data_len < 0 or data_len > 0xFFFFFFFF:
        raise ValueError("data length must fit the base16c-crc16-rs unsigned 32-bit header")
    return sum(_encoding_shape(data_len, line_width, parity_ratio)[-2:])


class StreamShape(_ty.NamedTuple):
    """Realized Reed-Solomon shape of an encoded ``L``/``P`` line stream.

    ``nsym``/``nblocks`` are ``None`` when the stream shape is ambiguous
    (``_candidate_nsym`` returned other than exactly one candidate) -- never
    guessed. ``nsym`` is the per-interleaved-block erasure budget.
    """

    data_lines: int
    parity_lines: int
    nsym: _ty.Optional[int]
    nblocks: _ty.Optional[int]
    data_bytes: int
    parity_bytes: int


def describe_line_stream(lines: _ty.Iterable[str]) -> StreamShape:
    """Report the realized RS shape of an encoded line stream, read-only.

    Mirrors :meth:`Base16CCodec.decode_spool`'s modal-width bookkeeping (widest
    payload among non-last lines sets ``bytes_per_line``) to compute the data
    and parity byte totals, then derives ``nsym``/``nblocks`` from
    :func:`_candidate_nsym`/:func:`_num_blocks`. It never corrects, decodes, or
    writes anything -- it exists so callers (e.g. ``glyphive inspect``) can
    report a document's per-line redundancy without a full decode. If the
    stream cannot be interpreted (no data lines, or an ambiguous nsym), the
    RS fields are ``None`` rather than a guess.
    """
    data: _ty.Dict[int, "_ParsedLine"] = {}
    parity: _ty.Dict[int, "_ParsedLine"] = {}
    for raw in lines:
        parsed = _parse_line(raw)
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
        bytes_per_line = (modal * 4) // 8
        total = 0
        for i in range(max_idx + 1):
            entry = index.get(i)
            if entry is None:
                total += bytes_per_line
                continue
            total += _payload_byte_len(entry.payload, bytes_per_line, i == max_idx)
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
    )


def _frame_stream(kind: str, source, length: int, bytes_per_line: int,
                  spec: "_RadixSpec" = BASE16C):
    index = 0
    remaining = length
    while remaining:
        chunk = source.read(min(bytes_per_line, remaining))
        if not chunk:
            raise ValueError(f"truncated {kind} spool during framing")
        remaining -= len(chunk)
        if index >= spec.max_idx:
            raise ValueError(f"{kind} stream exceeds {spec.max_idx} lines")
        yield _frame(kind, index, radix_encode(chunk, spec), spec)
        index += 1
    if source.read(1):
        raise ValueError(f"{kind} spool has trailing bytes")


def _read_spooled_line(source, offset: int, spec: "_RadixSpec" = BASE16C) -> _ParsedLine:
    source.seek(offset)
    parsed = _parse_line(source.readline().decode("utf-8").rstrip("\r\n"), spec)
    if parsed is None:
        raise ValueError("indexed codec line is no longer parseable")
    return parsed


def _assemble_to_spool(source, index, sink, bytes_per_line: int,
                       spec: "_RadixSpec" = BASE16C):
    erasures = []
    if not index:
        return 0, erasures
    max_idx = max(index)
    written = 0
    for idx in range(max_idx + 1):
        entry = index.get(idx)
        parsed = _read_spooled_line(source, entry[0], spec) if entry is not None else None
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
            chunk = b"\x00" * span
            erasures.extend(range(written, written + span))
        sink.write(chunk)
        written += span
    return written, erasures


def _copy_stream(source, sink, chunk_size=1024 * 1024):
    source.seek(0)
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            return
        sink.write(chunk)


def _assemble(
    parsed: _ty.Dict[int, "_ParsedLine"],
    bytes_per_line: int,
) -> _ty.Tuple[bytearray, _ty.List[int]]:
    """Reassemble a byte stream from indexed lines; collect erasure positions.

    Returns ``(bytes, erasure_positions)``. Missing indices and CRC-failed lines
    contribute their byte span to ``erasure_positions`` (filled with zero bytes)
    so RS can attempt correction. Line labels for error messages are recomputed
    in :func:`_first_failed_label`.
    """
    out = bytearray()
    erasures: _ty.List[int] = []
    if not parsed:
        return out, erasures

    max_idx = max(parsed)
    for idx in range(max_idx + 1):
        line = parsed.get(idx)
        base = len(out)
        is_last = idx == max_idx
        if line is not None and line.ok:
            # A trustworthy line: decode its payload to bytes.
            span = _payload_byte_len(line.payload, bytes_per_line, is_last)
            try:
                chunk = nibble_decode(line.payload, span)
            except ValueError:
                # Payload has an illegal char despite a matching CRC — treat as
                # erasure (extremely unlikely; CRC would normally catch it).
                chunk = b"\x00" * span
                erasures.extend(range(base, base + span))
            out.extend(chunk)
        else:
            # Missing or CRC-failed: reserve the expected byte span as erasures.
            # For a CRC-failed line we know its printed width; for a missing line
            # we assume a full-width line (the common case) — a wrong guess only
            # matters if the *final* line is the missing one, which shifts length
            # and will surface as a header/length error rather than silent loss.
            if line is not None:
                span = _payload_byte_len(line.payload, bytes_per_line, is_last)
            else:
                span = bytes_per_line
            out.extend(b"\x00" * span)
            erasures.extend(range(base, base + span))
    return out, erasures


def _payload_byte_len(
    payload: str, bytes_per_line: int, is_last: bool, spec: "_RadixSpec" = BASE16C
) -> int:
    """Bytes carried by a payload: full lines carry ``bytes_per_line``; the last
    line carries what its (possibly shorter) width encodes."""
    if not is_last:
        return bytes_per_line
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


class Base16CCodec(Codec):
    """The ``base16c-crc16-rs`` codec: 16-char OCR-safe alphabet / CRC-16-CCITT / Reed-Solomon.

    This is also the shared base for the denser radix codecs (``base8``/``base32``/
    ``base64``): they subclass it, overriding only ``name`` and ``_spec``. All the
    RS/header/spool machinery is radix-agnostic and driven by ``self._spec``.
    """

    name = "base16c-crc16-rs"

    #: The radix parameters this codec frames with. Subclasses override this
    #: (and ``name``) to get a denser alphabet; everything else is inherited.
    _spec: _ty.ClassVar["_RadixSpec"] = BASE16C

    def encode(
        self,
        data: bytes,
        *,
        line_width: int = 60,
        parity_ratio: float = 0.12,
    ) -> _ty.List[str]:
        """Encode bytes into OCR-safe framed data and parity lines."""
        if line_width < 1:
            raise ValueError("line_width must be >= 1")
        if not 0 < parity_ratio < 1:
            raise ValueError("parity_ratio must be in (0, 1)")

        orig_len = len(data)
        protected_len = _HEADER_LEN + orig_len
        nsym = _select_nsym(protected_len, parity_ratio)
        header = _make_header(nsym, orig_len)
        stream = header + data
        data_bytes, parity_bytes, _nblocks = _rs_encode(stream, nsym)

        lines: _ty.List[str] = []
        lines.extend(_frame_bytes("L", data_bytes, line_width, self._spec))
        lines.extend(_frame_bytes("P", parity_bytes, line_width, self._spec))
        return lines

    def iter_encode(
        self,
        source: _ty.BinaryIO,
        data_len: int,
        *,
        line_width: int = 60,
        parity_ratio: float = 0.12,
        temp_dir: _ty.Optional[str] = None,
    ) -> _ty.Iterator[str]:
        """Encode a seekable payload source with bounded Python allocations.

        The existing interleaved RS layout is preserved exactly. A temporary
        parity spool avoids retaining parity or framed lines in memory; mmap is
        used when the source exposes a file descriptor so each RS codeword is
        at most 255 bytes in Python memory.
        """
        if data_len < 0 or data_len > 0xFFFFFFFF:
            raise ValueError("data length must fit the base16c-crc16-rs unsigned 32-bit header")
        (
            bytes_per_line,
            protected_len,
            nsym,
            nblocks,
            _data_lines,
            _parity_lines,
        ) = _encoding_shape(data_len, line_width, parity_ratio, self._spec)
        header = _make_header(nsym, data_len)
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
            with _tempfile.TemporaryFile(dir=temp_dir) as parity:
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
                    parity.write(encoded[len(block):])

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
                    yield _frame("L", line_index, radix_encode(chunk, self._spec), self._spec)
                    line_index += 1
                if source.read(1):
                    raise ValueError("source grew during data framing")

                parity.seek(0)
                yield from _frame_stream(
                    "P", parity, nblocks * nsym, bytes_per_line, self._spec
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
        temp_dir: _ty.Optional[str] = None,
    ) -> None:
        """Decode an encoded-line spool with only offsets and RS blocks in RAM."""
        data_lines: _ty.Dict[int, _ty.Tuple[int, bool, int]] = {}
        parity_lines: _ty.Dict[int, _ty.Tuple[int, bool, int]] = {}
        # Per-index payload seen for a CRC-passing line, to detect a *conflicting*
        # collision (finding #3): a corrupted label that decodes to a real but
        # wrong index would silently overwrite a different genuine line under
        # blind last-write-wins. Track (payload) so we can distinguish a benign
        # duplicate (same bytes re-read) from a true conflict.
        ok_payload: _ty.Dict[_ty.Tuple[str, int], str] = {}
        collisions: _ty.List[str] = []
        length_counts: _ty.Counter[int] = _collections.Counter()
        encoded_source.seek(0)
        while True:
            offset = encoded_source.tell()
            raw = encoded_source.readline()
            if not raw:
                break
            parsed = _parse_line(raw.decode("utf-8").rstrip("\r\n"), self._spec)
            if parsed is None:
                continue
            target = data_lines if parsed.kind == "L" else parity_lines
            if parsed.ok:
                key = (parsed.kind, parsed.idx)
                prior = ok_payload.get(key)
                if prior is not None and prior != parsed.payload:
                    collisions.append(f"{parsed.kind}{parsed.idx:05d}")
                else:
                    ok_payload[key] = parsed.payload
            existing = target.get(parsed.idx)
            # Prefer a CRC-passing line over a CRC-failing one for the same index
            # instead of blind last-write-wins; only overwrite if the new line is
            # at least as trustworthy (ok) as what's already there.
            if existing is None or parsed.ok or not existing[1]:
                target[parsed.idx] = (offset, parsed.ok, len(parsed.payload))
            length_counts[len(parsed.payload)] += 1
        if collisions:
            unique = sorted(set(collisions))
            raise CodecError(
                "conflicting duplicate line index(es) "
                + ", ".join(unique)
                + ": two CRC-valid lines claim the same index with different "
                "payloads (likely an OCR-corrupted label decoding to a real but "
                "wrong index); refusing to silently discard one"
            )

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
        bytes_per_line = (modal_payload * self._spec.bits) // 8
        with _tempfile.TemporaryFile(dir=temp_dir) as data_spool, _tempfile.TemporaryFile(
            dir=temp_dir
        ) as parity_spool:
            data_len, data_erasures = _assemble_to_spool(
                encoded_source, data_lines, data_spool, bytes_per_line, self._spec
            )
            parity_len, parity_erasures = _assemble_to_spool(
                encoded_source, parity_lines, parity_spool, bytes_per_line, self._spec
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
                        hdr_nsym, orig_len = _parse_header(prefix)
                    except ValueError:
                        hdr_nsym, orig_len = None, None
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
                    with _tempfile.TemporaryFile(dir=temp_dir) as corrected:
                        _copy_stream(data_spool, corrected)
                        rs = _reedsolo.RSCodec(nsym)
                        failed = False
                        for block_index in range(nblocks):
                            positions = list(range(block_index, data_len, nblocks))
                            block_data = bytes(data_map[pos] for pos in positions)
                            parity_base = block_index * nsym
                            block_parity = bytes(
                                parity_map[pos] if pos < parity_len else 0
                                for pos in range(parity_base, parity_base + nsym)
                            )
                            erase_pos = [
                                local for local, pos in enumerate(positions)
                                if pos in data_erasure_set
                            ]
                            erase_pos.extend(
                                len(positions) + offset
                                for offset in range(nsym)
                                if parity_base + offset in parity_erasure_set
                                or parity_base + offset >= parity_len
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
                            hdr_nsym, orig_len = _parse_header(prefix)
                        except ValueError:
                            continue
                        if hdr_nsym != nsym or orig_len > data_len - _HEADER_LEN:
                            continue
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
