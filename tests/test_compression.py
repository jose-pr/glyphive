"""Tests for the named whole-stream compression registry."""

import gzip
import io

import pytest

from glyphive import compression
from glyphive.compression import zstd


def test_registry_names_and_fresh_instances():
    assert compression.names() == ["gzip", "none", "zstd"]
    assert compression.get("none") is not compression.get("none")
    assert compression.get("none").compress(b"payload") == b"payload"


def test_gzip_bytes_match_stdlib_except_normalized_os_byte():
    payload = b"compression registry" * 20
    actual = compression.get("gzip").compress(payload)
    expected = gzip.compress(payload, compresslevel=9, mtime=0)
    # CPython 3.11/3.12 delegates this fast path to zlib and writes the host OS
    # byte (3 on Linux), while GzipFile deliberately writes 255.  Glyphive uses
    # GzipFile so archives are byte-identical across operating systems.
    assert actual[:9] == expected[:9]
    assert actual[9] == 255
    assert actual[10:] == expected[10:]
    assert gzip.decompress(actual) == payload
    assert compression.get("gzip").decompress(
        compression.get("gzip").compress(payload, 1)
    ) == payload


@pytest.mark.parametrize("name", ["none", "gzip", "zstd"])
def test_streaming_compression_roundtrip_is_deterministic(name):
    payload = bytes(range(251)) * 400
    method = compression.get(name)
    outputs = []
    for _ in range(2):
        sink = io.BytesIO()
        method.compress_stream(io.BytesIO(payload), sink, chunk_size=97)
        outputs.append(sink.getvalue())
    assert outputs[0] == outputs[1]

    restored = io.BytesIO()
    method.decompress_stream(io.BytesIO(outputs[0]), restored, chunk_size=113)
    assert restored.getvalue() == payload


def test_streaming_compression_rejects_bad_chunk_size():
    with pytest.raises(ValueError, match="positive integer"):
        compression.get("none").compress_stream(io.BytesIO(), io.BytesIO(), chunk_size=0)


def test_unknown_compression_names_are_actionable():
    with pytest.raises(ValueError, match=r"unknown compression method 'missing'.*gzip"):
        compression.get("missing")


def test_default_selection_uses_zstd_availability(monkeypatch):
    monkeypatch.setattr(compression.ZstdCompression, "is_available", classmethod(lambda cls: True))
    assert compression.default() == "zstd"
    monkeypatch.setattr(compression.ZstdCompression, "is_available", classmethod(lambda cls: False))
    assert compression.default() == "gzip"


def test_missing_zstd_backend_has_install_hint(monkeypatch):
    def missing_backend():
        raise RuntimeError("zstd compression requires the optional 'zstandard' dependency")

    monkeypatch.setattr(zstd, "_zstd_module", missing_backend)
    with pytest.raises(RuntimeError, match=r"pip install glyphive\[zstd\]"):
        compression.get("zstd").compress(b"payload")


def test_duplicate_compression_names_are_rejected():
    existing = dict(compression.CompressionMethod._registry)
    try:
        with pytest.raises(ValueError, match="duplicate compression name 'none'"):
            type(
                "DuplicateCompression",
                (compression.CompressionMethod,),
                {
                    "name": "none",
                    "compress": lambda self, data, level=None: data,
                    "decompress": lambda self, data: data,
                },
            )
    finally:
        compression.CompressionMethod._registry.clear()
        compression.CompressionMethod._registry.update(existing)
