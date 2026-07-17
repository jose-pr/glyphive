"""Tests for the isolated QR envelope and optional image adapter."""

import hashlib
import io
import sys

import pytest

from glyphive.restore.qr import (
    MAX_PAYLOAD_BYTES,
    QrChunk,
    QrTransportError,
    chunk_transcript,
    pack_chunk,
    reassemble_symbols,
    symbol_png,
    symbols_from_image,
    unpack_chunk,
)


def test_multichunk_transcript_reassembles_out_of_order():
    transcript = bytes(range(256)) * 9
    digest = hashlib.sha256(transcript).digest()
    symbols = chunk_transcript(transcript, digest)

    assert len(symbols) == 3
    assert all(len(symbol) <= 1000 for symbol in symbols)
    assert reassemble_symbols(reversed(symbols)) == transcript


def test_empty_transcript_has_one_authenticated_chunk():
    digest = hashlib.sha256(b"").hexdigest()
    symbols = chunk_transcript(b"", digest)

    assert len(symbols) == 1
    assert unpack_chunk(symbols[0]).payload == b""
    assert reassemble_symbols(symbols) == b""


@pytest.mark.parametrize("mutation", ["magic", "length", "payload"])
def test_corrupt_envelope_is_rejected(mutation):
    symbol = bytearray(
        pack_chunk(QrChunk(hashlib.sha256(b"doc").digest(), 0, 1, b"payload"))
    )
    if mutation == "magic":
        symbol[0] ^= 1
    elif mutation == "length":
        symbol[43] ^= 1
    else:
        symbol[-1] ^= 1

    with pytest.raises(QrTransportError):
        unpack_chunk(symbol)


def test_missing_duplicate_and_mixed_document_symbols_are_rejected():
    one = chunk_transcript(b"x" * (MAX_PAYLOAD_BYTES + 1), hashlib.sha256(b"one").digest())
    two = chunk_transcript(b"other", hashlib.sha256(b"two").digest())

    with pytest.raises(QrTransportError, match="missing"):
        reassemble_symbols(one[:1])
    with pytest.raises(QrTransportError, match="duplicate"):
        reassemble_symbols([one[0], one[0]])
    with pytest.raises(QrTransportError, match="different documents"):
        reassemble_symbols([one[0], two[0]])


def test_base_envelope_import_does_not_load_optional_backends():
    assert "segno" not in sys.modules
    assert "zxingcpp" not in sys.modules


def test_real_png_roundtrip_when_qr_extra_is_installed():
    segno = pytest.importorskip("segno")
    pytest.importorskip("zxingcpp")
    image_module = pytest.importorskip("PIL.Image")
    assert segno.__version__ == "1.6.6"
    transcript = b"exact transcript\nwith form feed\fnext page\n"
    symbol = chunk_transcript(transcript, hashlib.sha256(transcript).digest())[0]

    with image_module.open(io.BytesIO(symbol_png(symbol, scale=6))) as image:
        decoded = symbols_from_image(image)

    assert decoded == [symbol]
    assert reassemble_symbols(decoded) == transcript
