"""glyphive codec ``g1`` — byte stream ↔ OCR-safe printable text lines.

This module is the core of the format. The alphabet is confusable-free, every
line carries its own CRC check, and Reed-Solomon parity lets small OCR errors be
corrected rather than silently propagating. It is pure: bytes in, list-of-str
lines out (and back). No file I/O, no ``pathlib_next``, no ``argparse`` here.

Frame grammar (one printed line)
--------------------------------
Every printed line has exactly this shape (single ASCII spaces as separators)::

    <kind><idx5> <payload> #<check4>

- ``<kind>``   : ``L`` for a data line, ``P`` for a Reed-Solomon parity line.
- ``<idx5>``   : the line's 0-based index within its stream (data lines and
                 parity lines are indexed independently, each starting at 0),
                 base-10, zero-padded to 5 digits (``00000``..``99999``). The
                 digits ``0``-``9`` are all in the safe alphabet.
- ``<payload>``: exactly ``line_width`` Crockford-Base32 characters, EXCEPT the
                 last data line, which may be shorter (it carries the remainder).
                 Parity lines are always exactly ``line_width`` wide.
- ``#``        : a literal ``#`` marking the start of the check field.
- ``<check4>`` : 4 Crockford-Base32 characters (20 bits) encoding a 16-bit
                 CRC-16/CCITT (poly 0x1021, init 0xFFFF) computed over the bytes
                 ``f"{idx:05d}".encode() + payload.encode()`` — i.e. over the
                 *printed* index digits and the *printed* payload characters, so
                 a human can recompute it from the page by hand.

Why 4 check characters (not 2)
------------------------------
A CRC-16 is a 16-bit value (0..65535). Crockford-Base32 carries 5 bits per
character, so 2 characters hold only 10 bits (1024 values) — far too few to
represent a 16-bit CRC without collisions/loss. 4 characters hold 20 bits, which
comfortably contains the full 16-bit CRC with no truncation. We therefore use a
**CRC-16/CCITT rendered as 4 Crockford characters** (the top 4 bits of the
20-bit field are always zero). This detects any single-character OCR substitution
in a line and localizes it to exactly that one line without decoding anything
downstream; the embedded ``idx5`` catches OCR line-merge / line-drop.

Header layout of the encoded stream
-----------------------------------
The very first bytes of a group's payload carry an 8-byte binary header so decode
can reconstruct the exact original byte length (Crockford bit-packing pads the
final 5-bit group, so the raw length must be carried, never guessed):

    b"G1" | version:u8 | nsym:u8 | orig_len:u32-big-endian

The header bytes are part of the RS-protected data stream, so they are covered by
parity and per-line CRC just like the payload.

Reed-Solomon parity
-------------------
Parity is computed over the group's data bytes (header + original bytes) with
``reedsolo.RSCodec(nsym)``. The data is split into interleaved RS blocks of at
most 255 bytes so arbitrarily large pages are supported while each block stays
within the GF(2^8) code length. ``nsym = clamp(round(k * parity_ratio), 2, 100)``
parity symbols per block, where ``k`` is that block's data length. Because the
per-line CRC tells decode *exactly which* lines are wrong, decode feeds those
lines' byte positions to RS as **erasures** — RS corrects up to ``nsym`` erasures
per block (twice its blind-error budget).

Decode oracle discipline (CRITICAL)
-----------------------------------
Correctness is judged SOLELY by the per-line CRC check and RS correction. There
is deliberately no "try confusable substitutions and keep whatever decompresses
further" search anywhere in this module. If a line's CRC fails AND RS cannot
correct the resulting erasures, ``decode`` RAISES a clear exception naming the
exact failing line label. It never guesses or mutates data to make it "work".
"""

import typing as _ty

import reedsolo as _reedsolo

from ._base import Codec

__all__ = [
    "ALPHABET",
    "CodecError",
    "G1Codec",
    "crockford_encode",
    "crockford_decode",
]

# ---------------------------------------------------------------------------
# Crockford Base32 alphabet
# ---------------------------------------------------------------------------

#: Crockford Base32 encode alphabet — excludes I, L, O, U to avoid confusables.
ALPHABET: _ty.Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Decode map: canonical chars, case-insensitive, plus confusable aliases
# (I,i,L,l -> 1 ; O,o -> 0). Built once at import time.
_DECODE_MAP: _ty.Final[_ty.Dict[str, int]] = {}
for _i, _ch in enumerate(ALPHABET):
    _DECODE_MAP[_ch] = _i
    _DECODE_MAP[_ch.lower()] = _i
for _alias, _target in (("I", "1"), ("L", "1"), ("O", "0")):
    _DECODE_MAP[_alias] = _DECODE_MAP[_target]
    _DECODE_MAP[_alias.lower()] = _DECODE_MAP[_target]
del _i, _ch, _alias, _target


class CodecError(ValueError):
    """Raised when a line fails its CRC and RS cannot correct it.

    Subclasses :class:`ValueError` so callers may catch either. The message
    always names the exact failing line label (e.g. ``L00042``).
    """


def crockford_encode(data: bytes) -> str:
    """Encode raw bytes to a Crockford-Base32 string (MSB-first, padded).

    The final 5-bit group is zero-padded. This is *not* self-delimiting: the
    caller must track the original byte length to strip pad bits on decode
    (:func:`crockford_decode` takes that length explicitly).
    """
    if not data:
        return ""
    out: _ty.List[str] = []
    acc = 0
    nbits = 0
    for byte in data:
        acc = (acc << 8) | byte
        nbits += 8
        while nbits >= 5:
            nbits -= 5
            out.append(ALPHABET[(acc >> nbits) & 0x1F])
    if nbits:  # flush the remaining bits, left-aligned (MSB-first) into a group
        out.append(ALPHABET[(acc << (5 - nbits)) & 0x1F])
    return "".join(out)


def crockford_decode(text: str, byte_len: int) -> bytes:
    """Decode a Crockford-Base32 string back to exactly ``byte_len`` bytes.

    Case-insensitive; maps confusables ``I/i/L/l -> 1`` and ``O/o -> 0``. Any
    character outside the alphabet (after alias mapping) raises ``ValueError``.
    Trailing pad bits beyond ``byte_len`` bytes are discarded.
    """
    if byte_len == 0:
        return b""
    acc = 0
    nbits = 0
    out = bytearray()
    for ch in text:
        try:
            val = _DECODE_MAP[ch]
        except KeyError:
            raise ValueError(f"invalid Crockford character {ch!r}") from None
        acc = (acc << 5) | val
        nbits += 5
        if nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
        if len(out) == byte_len:
            break
    if len(out) < byte_len:
        raise ValueError(
            f"Crockford payload too short: got {len(out)} bytes, need {byte_len}"
        )
    return bytes(out)


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


#: Number of Crockford characters in a line's check field. 4 chars = 20 bits,
#: enough to hold a full 16-bit CRC-16 without truncation (2 chars = 10 bits
#: would not). Documented at module top under "Why 4 check characters".
CHECK_WIDTH: _ty.Final[int] = 4


def _check_chars(idx: int, payload: str) -> str:
    """Compute the 4-char Crockford check field for a framed line."""
    crc = _crc16_ccitt(f"{idx:05d}".encode() + payload.encode())
    chars = []
    for shift in range(CHECK_WIDTH - 1, -1, -1):
        chars.append(ALPHABET[(crc >> (5 * shift)) & 0x1F])
    return "".join(chars)


# ---------------------------------------------------------------------------
# Line framing
# ---------------------------------------------------------------------------

_MAX_IDX: _ty.Final[int] = 100_000  # idx5 is 5 decimal digits
_RS_BLOCK: _ty.Final[int] = 255  # GF(2^8) code length ceiling

# Letter-for-digit OCR reads, for the index field only -- it is decimal by
# construction, so a letter there is always a misread. Deliberately has no entry
# for the kind character: a label's ``L`` must stay ``L``, never become ``1``.
_OCR_DIGIT_MAP: _ty.Final[_ty.Dict[str, str]] = {
    "o": "0",
    "O": "0",
    "Q": "0",
    "D": "0",
    "i": "1",
    "I": "1",
    "l": "1",
    "S": "5",
    "s": "5",
    "B": "8",
}


def normalize_index_digits(idx_text: str) -> str:
    """Canonicalize OCR letter-for-digit reads in a framed line's index field.

    ``LO0000`` -> index ``00000``. Local, deterministic normalization of one
    closed decimal field -- not a repair search: the per-line CRC is recomputed
    from the canonical ``{idx:05d}`` and still decides whether the line is right,
    so a wrong normalization fails its check and becomes an RS erasure.
    """
    return "".join(_OCR_DIGIT_MAP.get(char, char) for char in idx_text)


def _frame(kind: str, idx: int, payload: str) -> str:
    return f"{kind}{idx:05d} {payload} #{_check_chars(idx, payload)}"


class _ParsedLine(_ty.NamedTuple):
    kind: str  # "L" or "P"
    idx: int
    payload: str
    ok: bool  # True iff the CRC check field matched


def _parse_line(line: str) -> _ty.Optional[_ParsedLine]:
    """Parse one framed line. Returns None for blank/foreign lines.

    ``ok`` reflects whether the printed check field matches a freshly computed
    CRC over the (alias-normalized) index+payload. A structurally broken line
    (missing fields, bad kind) is treated as a failed check on a best-effort
    index so decode can still localize it; if even the index is unreadable the
    line is skipped (its absence surfaces later as a length/erasure error).
    """
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.split()
    if len(parts) != 3:
        return None
    label, payload, check = parts
    if not check.startswith("#"):
        return None
    kind = label[:1]
    if kind not in ("L", "P"):
        return None
    # The index is decimal by construction, so an OCR letter-for-digit read
    # (``LO0000``) is normalized before matching. The CRC below is recomputed
    # from the canonical ``{idx:05d}``, so a wrong normalization just fails its
    # check and becomes an erasure — it can never smuggle a bad line through.
    idx_text = normalize_index_digits(label[1:])
    if not (idx_text.isdigit() and len(idx_text) == 5):
        return None
    idx = int(idx_text)
    expected = _check_chars(idx, payload)
    ok = check[1:].upper() == expected
    return _ParsedLine(kind=kind, idx=idx, payload=payload, ok=ok)


# ---------------------------------------------------------------------------
# Group header (carries exact original length + RS params)
# ---------------------------------------------------------------------------

_MAGIC: _ty.Final[bytes] = b"G1"
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


def _frame_bytes(kind: str, data: bytes, line_width: int) -> _ty.List[str]:
    """Crockford-encode ``data`` and split into framed lines of ``line_width``.

    Each line's payload maps back to a whole number of bytes: we chunk the byte
    stream so that ``line_width`` Crockford chars carry a fixed byte count where
    possible, and the final line carries whatever remains. To keep byte↔line
    mapping exact and simple, we pack a fixed number of bytes per line such that
    they encode to at most ``line_width`` chars.
    """
    if not data:
        return []
    # bytes per full line: the largest B with ceil(8B/5) <= line_width.
    bytes_per_line = (line_width * 5) // 8
    if bytes_per_line < 1:
        raise ValueError("line_width too small to carry a byte")
    lines: _ty.List[str] = []
    idx = 0
    for start in range(0, len(data), bytes_per_line):
        chunk = data[start:start + bytes_per_line]
        payload = crockford_encode(chunk)
        if idx >= _MAX_IDX:
            raise ValueError(
                f"{kind} stream exceeds {_MAX_IDX} lines; use smaller pages"
            )
        lines.append(_frame(kind, idx, payload))
        idx += 1
    return lines


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
                chunk = crockford_decode(line.payload, span)
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


def _payload_byte_len(payload: str, bytes_per_line: int, is_last: bool) -> int:
    """Bytes carried by a payload: full lines carry ``bytes_per_line``; the last
    line carries what its (possibly shorter) width encodes."""
    if not is_last:
        return bytes_per_line
    # Last line: derive byte count from its own char width (floor(5*chars/8)).
    return (len(payload) * 5) // 8


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


class G1Codec(Codec):
    """The stable ``g1`` codec implementation."""

    name = "g1"

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
        nsym = max(2, min(100, round(protected_len * parity_ratio)))
        header = _make_header(nsym, orig_len)
        stream = header + data
        data_bytes, parity_bytes, _nblocks = _rs_encode(stream, nsym)

        lines: _ty.List[str] = []
        lines.extend(_frame_bytes("L", data_bytes, line_width))
        lines.extend(_frame_bytes("P", parity_bytes, line_width))
        return lines

    def decode(self, lines: _ty.Iterable[str]) -> bytes:
        """Decode framed lines using only CRC and RS as correctness oracles."""
        data_lines: _ty.Dict[int, _ParsedLine] = {}
        parity_lines: _ty.Dict[int, _ParsedLine] = {}
        for raw in lines:
            parsed = _parse_line(raw)
            if parsed is None:
                continue
            target = data_lines if parsed.kind == "L" else parity_lines
            target[parsed.idx] = parsed

        if not data_lines:
            raise ValueError("no data lines found to decode")

        all_parsed = list(data_lines.values()) + list(parity_lines.values())
        max_payload = max(len(p.payload) for p in all_parsed)
        bytes_per_line = (max_payload * 5) // 8
        data_bytes, data_erasures = _assemble(data_lines, bytes_per_line)
        parity_bytes, parity_erasures = _assemble(parity_lines, bytes_per_line)

        if len(data_bytes) < _HEADER_LEN:
            raise ValueError("data stream shorter than the group header")

        candidates = _candidate_nsym(len(data_bytes), len(parity_bytes))
        if not candidates:
            raise ValueError(
                "cannot recover RS parameters: data/parity line counts are "
                "inconsistent (missing or spurious lines)"
            )

        rs_budget_exceeded = False
        for nsym in candidates:
            nblocks = _num_blocks(len(data_bytes), nsym)
            expected_parity_len = nblocks * nsym
            trial_data = bytearray(data_bytes)
            trial_parity = bytearray(parity_bytes)
            trial_parity_erasures = list(parity_erasures)
            if len(trial_parity) < expected_parity_len:
                for pos in range(len(trial_parity), expected_parity_len):
                    trial_parity_erasures.append(pos)
                trial_parity.extend(
                    b"\x00" * (expected_parity_len - len(trial_parity))
                )
            elif len(trial_parity) > expected_parity_len:
                trial_parity = trial_parity[:expected_parity_len]

            try:
                corrected = _rs_decode(
                    trial_data,
                    data_erasures,
                    trial_parity,
                    trial_parity_erasures,
                    nsym,
                    nblocks,
                )
            except _reedsolo.ReedSolomonError:
                rs_budget_exceeded = True
                continue

            try:
                hdr_nsym, orig_len = _parse_header(corrected)
            except ValueError:
                continue
            if hdr_nsym != nsym or orig_len > len(corrected) - _HEADER_LEN:
                continue
            return corrected[_HEADER_LEN:_HEADER_LEN + orig_len]

        if rs_budget_exceeded:
            bad = _first_failed_label(data_lines, parity_lines)
            raise CodecError(
                f"line {bad} failed CRC and exceeds RS correction budget"
            )
        raise ValueError(
            "decode failed: corrected stream did not yield a valid group header"
        )
