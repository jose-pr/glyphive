"""Document-level (whole-page) Reed-Solomon parity.

A SEPARATE layer above the per-line codec RS: the line codec fixes scattered
OCR character errors on surviving pages; this fixes *whole lost pages* (a page
physically destroyed, unscannable, or dropped). Given ``D`` equal-size data
blocks (one per data page, its encoded lines joined and zero-padded to a fixed
block size ``B``) it computes ``K`` parity blocks; restore can then rebuild up
to ``K`` wholly missing data blocks, because a missing page's number gives the
RS erasure position for free (PAR2/RAID striping).

Two Galois fields are supported, selected by the ``c_exp`` keyword:

- ``c_exp=8`` (default): RS over GF(2^8), one byte per symbol. The codeword
  length ``D + K`` must not exceed 255. The math is column-wise: for each byte
  offset ``i`` in ``0..B``, the ``i``-th byte of every data block forms a
  length-``D`` message; RS-encoding it yields ``K`` parity bytes that become
  the ``i``-th byte of each parity block.
- ``c_exp=16``: RS over GF(2^16), one 16-bit symbol per *pair* of bytes,
  raising the codeword length cap to 65,535. Adjacent bytes are paired
  big-endian (``(block[2i] << 8) | block[2i+1]``). A *data* block with an odd
  number of bytes is zero-padded by one byte internally purely for symbol
  pairing -- that pad byte is never real content and both sides regenerate it
  identically, so returned/recovered data blocks are exactly ``block_bytes``
  long, unaffected. A *parity* block cannot receive the same treatment: its
  final symbol is genuine RS-computed information (not a deterministic
  placeholder), so truncating it would silently discard data needed for
  recovery. Parity blocks are therefore ``block_bytes`` rounded up to an even
  length -- one byte longer than the data blocks when ``block_bytes`` is odd,
  equal to it when even. Callers that always keep ``block_bytes`` even (as
  :mod:`glyphive.layout` does when it selects this field) never observe the
  asymmetry.

A wholly missing block is an erasure at that block's (known) position in
every column, regardless of field width.
"""

from __future__ import annotations

import typing as _ty

import reedsolo as _reedsolo

__all__ = [
    "MAX_TOTAL_BLOCKS",
    "PageParityError",
    "max_total_blocks",
    "encode_page_parity",
    "reconstruct_pages",
]

#: GF(2^8) caps the codeword length (data + parity blocks) at 255.
MAX_TOTAL_BLOCKS: _ty.Final[int] = 255

#: GF(2^16) caps the codeword length (data + parity blocks) at 65535.
_MAX_TOTAL_BLOCKS_GF216: _ty.Final[int] = 65535


class PageParityError(ValueError):
    """Raised when page-parity parameters are invalid or recovery is impossible."""


def max_total_blocks(c_exp: int) -> int:
    """Return the codeword-length cap (data + parity blocks) for ``c_exp``."""
    if c_exp == 8:
        return MAX_TOTAL_BLOCKS
    if c_exp == 16:
        return _MAX_TOTAL_BLOCKS_GF216
    raise PageParityError(f"unsupported page-parity field c_exp={c_exp!r}; must be 8 or 16")


def _check_shape(n_data: int, k: int, block_bytes: int, *, c_exp: int = 8) -> None:
    if k < 0:
        raise PageParityError("parity page count K must be >= 0")
    if n_data < 1:
        raise PageParityError("need at least one data block")
    limit = max_total_blocks(c_exp)
    if n_data + k > limit:
        raise PageParityError(
            f"data pages + parity pages ({n_data}+{k}) exceeds the "
            f"{limit}-block Reed-Solomon limit"
        )
    if block_bytes < 0:
        raise PageParityError("block_bytes must be >= 0")


def encode_page_parity(
    data_blocks: _ty.Sequence[bytes], k: int, *, c_exp: int = 8
) -> _ty.List[bytes]:
    """Return ``k`` parity blocks over ``data_blocks``.

    Every data block must already be exactly the same length ``B``. Returns an
    empty list when ``k == 0``. ``c_exp`` selects the Galois field (8 or 16;
    see the module docstring); the GF(2^8) path (the default) is unchanged
    from the pre-GF(2^16) implementation and produces byte-identical output.
    At ``c_exp=8`` every parity block is exactly ``B`` bytes; at ``c_exp=16``
    each is ``B`` rounded up to an even length (see the module docstring).
    """
    n = len(data_blocks)
    if k == 0:
        _check_shape(n, k, 0 if not data_blocks else len(data_blocks[0]), c_exp=c_exp)
        return []
    block_bytes = len(data_blocks[0]) if data_blocks else 0
    _check_shape(n, k, block_bytes, c_exp=c_exp)
    if any(len(b) != block_bytes for b in data_blocks):
        raise PageParityError("all data blocks must have identical length")

    if c_exp == 8:
        codec = _reedsolo.RSCodec(k)
        parity = [bytearray(block_bytes) for _ in range(k)]
        for i in range(block_bytes):
            column = bytes(block[i] for block in data_blocks)
            encoded = codec.encode(column)  # n data bytes + k parity bytes
            for j in range(k):
                parity[j][i] = encoded[n + j]
        return [bytes(p) for p in parity]

    # c_exp == 16 (validated by _check_shape above via max_total_blocks).
    codec = _reedsolo.RSCodec(k, c_exp=16)
    pad = block_bytes % 2
    padded_blocks = [b + b"\x00" if pad else b for b in data_blocks]
    n_symbols = (block_bytes + pad) // 2
    parity_symbols = [[0] * n_symbols for _ in range(k)]
    for i in range(n_symbols):
        lo = 2 * i
        column = [(block[lo] << 8) | block[lo + 1] for block in padded_blocks]
        encoded = codec.encode(column)  # n data symbols + k parity symbols
        for j in range(k):
            parity_symbols[j][i] = encoded[n + j]

    parity_blocks: _ty.List[bytes] = []
    for symbols in parity_symbols:
        out = bytearray(2 * n_symbols)
        for i, sym in enumerate(symbols):
            out[2 * i] = (sym >> 8) & 0xFF
            out[2 * i + 1] = sym & 0xFF
        # NOT truncated to block_bytes: unlike a data block's synthetic pad
        # byte (always zero, regenerable), a parity block's final symbol is
        # genuine RS output -- dropping half of it would silently corrupt
        # recovery (see module docstring).
        parity_blocks.append(bytes(out))
    return parity_blocks


def reconstruct_pages(
    blocks: _ty.Sequence[_ty.Optional[bytes]], k: int, *, c_exp: int = 8
) -> _ty.List[bytes]:
    """Reconstruct the ``D`` data blocks from ``D+K`` (some ``None``) blocks.

    ``blocks`` is ordered: indices ``0..D-1`` are data blocks, ``D..D+K-1`` are
    parity blocks; any entry may be ``None`` (missing). Returns the ``D``
    recovered data blocks. Raises :class:`PageParityError` if more than ``K``
    blocks are missing, or the surviving blocks disagree on their length.
    ``c_exp`` must match the value used at encode time (8 or 16; see the
    module docstring). At ``c_exp=16`` surviving data and parity blocks may
    legitimately differ in length by one byte (see the module docstring);
    each group's length is validated for internal consistency.
    """
    total = len(blocks)
    n_data = total - k
    if n_data < 1:
        raise PageParityError("need at least one data block position")
    _check_shape(n_data, k, 0, c_exp=c_exp)

    erase_positions = [idx for idx, b in enumerate(blocks) if b is None]
    if len(erase_positions) > k:
        raise PageParityError(
            f"{len(erase_positions)} blocks missing exceeds parity budget K={k}"
        )

    if c_exp == 8:
        present = [b for b in blocks if b is not None]
        if not present:
            raise PageParityError("no blocks present; cannot reconstruct")
        block_bytes = len(present[0])
        if any(len(b) != block_bytes for b in present):
            raise PageParityError("surviving blocks disagree on length")
        if not erase_positions:
            return [bytes(b) for b in blocks[:n_data]]  # nothing to do

        codec = _reedsolo.RSCodec(k)
        recovered_data = [bytearray(block_bytes) for _ in range(n_data)]
        zero = bytes(block_bytes)  # placeholder bytes for a missing block
        for i in range(block_bytes):
            codeword = bytearray(
                (blocks[idx] if blocks[idx] is not None else zero)[i]
                for idx in range(total)
            )
            decoded = codec.decode(codeword, erase_pos=list(erase_positions))[0]
            for idx in range(n_data):
                recovered_data[idx][i] = decoded[idx]
        return [bytes(b) for b in recovered_data]

    # c_exp == 16 (validated by _check_shape above via max_total_blocks).
    present_data = [blocks[idx] for idx in range(n_data) if blocks[idx] is not None]
    present_parity = [blocks[idx] for idx in range(n_data, total) if blocks[idx] is not None]
    if not present_data and not present_parity:
        raise PageParityError("no blocks present; cannot reconstruct")

    if present_data:
        block_bytes = len(present_data[0])
        if any(len(b) != block_bytes for b in present_data):
            raise PageParityError("surviving blocks disagree on length")
    else:
        # No data blocks survived at all (requires K >= n_data). A parity
        # block's length can't distinguish an odd block_bytes from
        # block_bytes - 1 without out-of-band info; assume block_bytes was
        # already even (true for every c_exp=16 document glyphive itself
        # writes -- see the module docstring).
        block_bytes = len(present_parity[0])
    padded_len = block_bytes + (block_bytes % 2)
    if any(len(b) != padded_len for b in present_parity):
        raise PageParityError("surviving blocks disagree on length")

    if not erase_positions:
        return [bytes(b) for b in blocks[:n_data]]  # nothing to do

    codec = _reedsolo.RSCodec(k, c_exp=16)
    pad = block_bytes % 2
    n_symbols = padded_len // 2
    zero = bytes(padded_len)  # placeholder bytes for a missing block

    def _slot(idx: int) -> bytes:
        b = blocks[idx]
        if b is None:
            return zero
        if idx < n_data and pad:
            return b + b"\x00"
        return b

    padded_blocks = [_slot(idx) for idx in range(total)]
    recovered_data = [bytearray(padded_len) for _ in range(n_data)]
    for i in range(n_symbols):
        lo = 2 * i
        codeword = [(block[lo] << 8) | block[lo + 1] for block in padded_blocks]
        decoded = codec.decode(codeword, erase_pos=list(erase_positions))[0]
        for idx in range(n_data):
            sym = decoded[idx]
            recovered_data[idx][lo] = (sym >> 8) & 0xFF
            recovered_data[idx][lo + 1] = sym & 0xFF
    return [bytes(b[:block_bytes]) for b in recovered_data]
