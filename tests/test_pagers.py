"""Tests for the whole-page Reed-Solomon parity primitive."""

import os
import time

import pytest
import reedsolo

from glyphive.codec import pagers


def test_encode_reconstruct_round_trip_recovers_up_to_k_missing():
    rng = os.urandom
    D, K, B = 6, 3, 48
    data = [rng(B) for _ in range(D)]
    parity = pagers.encode_page_parity(data, K)
    assert len(parity) == K and all(len(p) == B for p in parity)

    for drop in range(K + 1):
        blocks = list(data) + list(parity)
        for idx in range(drop):
            blocks[idx] = None  # drop data blocks 0..drop-1
        recovered = pagers.reconstruct_pages(blocks, K)
        assert recovered == data


def test_dropping_a_mix_of_data_and_parity_still_recovers():
    D, K, B = 5, 2, 32
    data = [os.urandom(B) for _ in range(D)]
    blocks = data + pagers.encode_page_parity(data, K)
    blocks[0] = None  # one data page
    blocks[D] = None  # one parity page
    assert pagers.reconstruct_pages(blocks, K) == data


def test_more_than_k_missing_raises():
    D, K, B = 4, 2, 16
    data = [os.urandom(B) for _ in range(D)]
    blocks = data + pagers.encode_page_parity(data, K)
    blocks[0] = blocks[1] = blocks[2] = None
    with pytest.raises(pagers.PageParityError, match="exceeds parity budget"):
        pagers.reconstruct_pages(blocks, K)


def test_k_zero_produces_no_parity_and_needs_all_blocks():
    data = [os.urandom(8) for _ in range(3)]
    assert pagers.encode_page_parity(data, 0) == []


def test_exceeding_gf256_block_limit_raises():
    with pytest.raises(pagers.PageParityError, match="Reed-Solomon limit"):
        pagers.encode_page_parity([b"x"] * 250, 10)  # 250 + 10 > 255


def test_reedsolo_gf216_erasure_decode_round_trips():
    """Plan step 1 verification: reedsolo's GF(2^16) *erasure decode* path
    (not just encode) round-trips a >255-symbol message. This was the
    blocking unknown for switching the page-parity layer to GF(2^16)."""
    codec = reedsolo.RSCodec(6, c_exp=16)
    msg = [(i * 37) % 65536 for i in range(300)]
    encoded = codec.encode(msg)
    damaged = list(encoded)
    erase = [3, 17, 100, 250, 299]
    for pos in erase:
        damaged[pos] = 0
    decoded = codec.decode(damaged, erase_pos=erase)[0]
    assert list(decoded) == msg


def test_gf216_encode_reconstruct_round_trip_with_more_than_255_blocks():
    """Property test mirroring the GF(2^8) round-trip test above, but at
    c_exp=16 with a block count that exceeds the GF(2^8) 255-block cap."""
    rng = os.urandom
    D, K, B = 300, 5, 18  # even block_bytes: matches how layout.py always calls this
    data = [rng(B) for _ in range(D)]
    parity = pagers.encode_page_parity(data, K, c_exp=16)
    assert len(parity) == K and all(len(p) == B for p in parity)

    blocks = list(data) + list(parity)
    for idx in range(K):
        blocks[idx] = None  # drop data blocks 0..K-1
    recovered = pagers.reconstruct_pages(blocks, K, c_exp=16)
    assert recovered == data


def test_gf216_odd_block_bytes_parity_is_one_byte_longer():
    """A data block's odd final byte is zero-padded internally and dropped
    again on the way out (recoverable, so no length change); a parity
    block's final RS symbol is genuine information and is NOT dropped, so
    parity blocks come out one byte longer than an odd block_bytes."""
    rng = os.urandom
    D, K, B = 12, 3, 17  # odd
    data = [rng(B) for _ in range(D)]
    parity = pagers.encode_page_parity(data, K, c_exp=16)
    assert all(len(p) == B + 1 for p in parity)

    blocks = list(data) + list(parity)
    for idx in range(K):
        blocks[idx] = None  # drop data blocks 0..K-1
    recovered = pagers.reconstruct_pages(blocks, K, c_exp=16)
    assert recovered == data
    assert all(len(b) == B for b in recovered)


def test_gf216_dropping_a_mix_of_data_and_parity_still_recovers():
    D, K, B = 260, 4, 10  # D alone already exceeds the GF(2^8) cap
    data = [os.urandom(B) for _ in range(D)]
    blocks = data + pagers.encode_page_parity(data, K, c_exp=16)
    blocks[0] = None  # one data page
    blocks[D] = None  # one parity page
    assert pagers.reconstruct_pages(blocks, K, c_exp=16) == data


def test_gf216_allows_more_than_255_total_blocks():
    # 300 data + 10 parity would be rejected at c_exp=8, but fits GF(2^16).
    data = [b"x" * 4] * 300
    parity = pagers.encode_page_parity(data, 10, c_exp=16)
    assert len(parity) == 10


def test_exceeding_gf216_block_limit_raises():
    with pytest.raises(pagers.PageParityError, match="Reed-Solomon limit"):
        pagers.encode_page_parity([b"x"] * 65530, 10, c_exp=16)  # > 65535


def test_unsupported_c_exp_raises():
    with pytest.raises(pagers.PageParityError, match="unsupported"):
        pagers.encode_page_parity([b"x"] * 4, 2, c_exp=12)


def test_max_total_blocks_helper():
    assert pagers.max_total_blocks(8) == 255
    assert pagers.max_total_blocks(16) == 65535
    with pytest.raises(pagers.PageParityError):
        pagers.max_total_blocks(4)


def test_gf216_large_case_encode_and_reconstruct_timing():
    """Plan 5 acceptance criterion: measure (not just eyeball) the GF(2^16)
    wall-time for a realistic large case -- 1,000 pages x 3,000-byte block,
    K=8 -- and keep it well inside the ~30s CI budget the plan calls out.
    Comfortably fast in practice (a few seconds total on pure-python
    reedsolo), so this runs as a normal (not skipped/manual) test.
    """
    D, K, B = 1000, 8, 3000
    data = [os.urandom(B) for _ in range(D)]

    t0 = time.time()
    parity = pagers.encode_page_parity(data, K, c_exp=16)
    encode_s = time.time() - t0
    assert len(parity) == K and all(len(p) == B for p in parity)

    blocks = list(data) + list(parity)
    for idx in range(K):
        blocks[idx] = None  # drop K data blocks -- the worst case for decode
    t1 = time.time()
    recovered = pagers.reconstruct_pages(blocks, K, c_exp=16)
    reconstruct_s = time.time() - t1
    assert recovered == data

    print(
        f"\n[gf216 timing] encode={encode_s:.2f}s reconstruct={reconstruct_s:.2f}s "
        f"(D={D}, B={B}, K={K})"
    )
    assert encode_s < 30, f"encode took {encode_s:.2f}s, exceeds the ~30s CI budget"
    assert reconstruct_s < 30, f"reconstruct took {reconstruct_s:.2f}s, exceeds the ~30s CI budget"
