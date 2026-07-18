"""Document-level (whole-page) Reed-Solomon parity.

A SEPARATE layer above the per-line codec RS: the line codec fixes scattered
OCR character errors on surviving pages; this fixes *whole lost pages* (a page
physically destroyed, unscannable, or dropped). Given ``D`` equal-size data
blocks (one per data page, its encoded lines joined and zero-padded to a fixed
block size ``B``) it computes ``K`` parity blocks; restore can then rebuild up
to ``K`` wholly missing data blocks, because a missing page's number gives the
RS erasure position for free (PAR2/RAID striping).

RS is over GF(2^8), so the codeword length ``D + K`` must not exceed 255.

The math is column-wise: for each byte offset ``i`` in ``0..B``, the ``i``-th
byte of every data block forms a length-``D`` message; RS-encoding it yields
``K`` parity bytes that become the ``i``-th byte of each parity block. A wholly
missing block is an erasure at that block's (known) position in every column.
"""

from __future__ import annotations

import typing as _ty

import reedsolo as _reedsolo

__all__ = [
    "MAX_TOTAL_BLOCKS",
    "PageParityError",
    "encode_page_parity",
    "reconstruct_pages",
]

#: GF(2^8) caps the codeword length (data + parity blocks) at 255.
MAX_TOTAL_BLOCKS: _ty.Final[int] = 255


class PageParityError(ValueError):
    """Raised when page-parity parameters are invalid or recovery is impossible."""


def _check_shape(n_data: int, k: int, block_bytes: int) -> None:
    if k < 0:
        raise PageParityError("parity page count K must be >= 0")
    if n_data < 1:
        raise PageParityError("need at least one data block")
    if n_data + k > MAX_TOTAL_BLOCKS:
        raise PageParityError(
            f"data pages + parity pages ({n_data}+{k}) exceeds the "
            f"{MAX_TOTAL_BLOCKS}-block Reed-Solomon limit"
        )
    if block_bytes < 0:
        raise PageParityError("block_bytes must be >= 0")


def encode_page_parity(data_blocks: _ty.Sequence[bytes], k: int) -> _ty.List[bytes]:
    """Return ``k`` parity blocks (each ``B`` bytes) over ``data_blocks``.

    Every data block must already be exactly the same length ``B``. Returns an
    empty list when ``k == 0``.
    """
    n = len(data_blocks)
    if k == 0:
        _check_shape(n, k, 0 if not data_blocks else len(data_blocks[0]))
        return []
    block_bytes = len(data_blocks[0]) if data_blocks else 0
    _check_shape(n, k, block_bytes)
    if any(len(b) != block_bytes for b in data_blocks):
        raise PageParityError("all data blocks must have identical length")

    codec = _reedsolo.RSCodec(k)
    parity = [bytearray(block_bytes) for _ in range(k)]
    for i in range(block_bytes):
        column = bytes(block[i] for block in data_blocks)
        encoded = codec.encode(column)  # n data bytes + k parity bytes
        for j in range(k):
            parity[j][i] = encoded[n + j]
    return [bytes(p) for p in parity]


def reconstruct_pages(
    blocks: _ty.Sequence[_ty.Optional[bytes]], k: int
) -> _ty.List[bytes]:
    """Reconstruct the ``D`` data blocks from ``D+K`` (some ``None``) blocks.

    ``blocks`` is ordered: indices ``0..D-1`` are data blocks, ``D..D+K-1`` are
    parity blocks; any entry may be ``None`` (missing). Returns the ``D``
    recovered data blocks. Raises :class:`PageParityError` if more than ``K``
    blocks are missing, or the surviving blocks disagree on their length.
    """
    total = len(blocks)
    n_data = total - k
    if n_data < 1:
        raise PageParityError("need at least one data block position")
    _check_shape(n_data, k, 0)

    present = [b for b in blocks if b is not None]
    if not present:
        raise PageParityError("no blocks present; cannot reconstruct")
    block_bytes = len(present[0])
    if any(len(b) != block_bytes for b in present):
        raise PageParityError("surviving blocks disagree on length")

    erase_positions = [idx for idx, b in enumerate(blocks) if b is None]
    if len(erase_positions) > k:
        raise PageParityError(
            f"{len(erase_positions)} blocks missing exceeds parity budget K={k}"
        )
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
