"""Tests for the whole-page Reed-Solomon parity primitive."""

import os

import pytest

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
