"""Self-validating QR transport for an unchanged Glyphive transcript.

The optional QR dependencies are imported only by image encode/decode calls.
Envelope parsing and transcript reassembly remain available in a base install.
"""

from __future__ import annotations

import hashlib
import io
import struct
import typing as _ty
from dataclasses import dataclass

from pathlib_next import Path


_MAGIC = b"GQ1"
_HEADER = struct.Struct(">3s32sIIH32s")
MAX_SYMBOL_BYTES = 1000
MAX_PAYLOAD_BYTES = MAX_SYMBOL_BYTES - _HEADER.size


class QrTransportError(ValueError):
    """A QR symbol set is malformed, inconsistent, or incomplete."""


@dataclass(frozen=True)
class QrChunk:
    """Validated contents of one Glyphive QR symbol."""

    document_digest: bytes
    index: int
    total: int
    payload: bytes


def _digest_bytes(value: _ty.Union[str, bytes]) -> bytes:
    if isinstance(value, str):
        try:
            value = bytes.fromhex(value)
        except ValueError as exc:
            raise QrTransportError("document digest must be hexadecimal") from exc
    if len(value) != 32:
        raise QrTransportError("document digest must contain exactly 32 bytes")
    return bytes(value)


def pack_chunk(chunk: QrChunk) -> bytes:
    """Serialize one validated chunk into the version-1 binary envelope."""
    digest = _digest_bytes(chunk.document_digest)
    payload = bytes(chunk.payload)
    if not 0 < chunk.total <= 0xFFFFFFFF:
        raise QrTransportError("QR chunk total must be between 1 and 2^32-1")
    if not 0 <= chunk.index < chunk.total:
        raise QrTransportError("QR chunk index must be smaller than its total")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise QrTransportError(
            f"QR payload exceeds the {MAX_PAYLOAD_BYTES}-byte envelope limit"
        )
    header = _HEADER.pack(
        _MAGIC,
        digest,
        chunk.index,
        chunk.total,
        len(payload),
        hashlib.sha256(payload).digest(),
    )
    return header + payload


def unpack_chunk(symbol: bytes) -> QrChunk:
    """Parse and authenticate one binary Glyphive QR envelope."""
    symbol = bytes(symbol)
    if len(symbol) < _HEADER.size:
        raise QrTransportError("QR symbol is shorter than the Glyphive envelope")
    if len(symbol) > MAX_SYMBOL_BYTES:
        raise QrTransportError("QR symbol exceeds the 1000-byte transport limit")
    magic, digest, index, total, length, payload_digest = _HEADER.unpack(
        symbol[: _HEADER.size]
    )
    if magic != _MAGIC:
        raise QrTransportError("QR symbol is not a Glyphive GQ1 envelope")
    payload = symbol[_HEADER.size :]
    if length != len(payload):
        raise QrTransportError(
            f"QR payload length mismatch: expected {length}, got {len(payload)}"
        )
    if not total or index >= total:
        raise QrTransportError("QR chunk index/total is invalid")
    if hashlib.sha256(payload).digest() != payload_digest:
        raise QrTransportError("QR payload SHA-256 mismatch")
    return QrChunk(digest, index, total, payload)


def chunk_transcript(
    transcript: bytes, document_digest: _ty.Union[str, bytes]
) -> _ty.List[bytes]:
    """Split exact transcript bytes into deterministic GQ1 symbol envelopes."""
    transcript = bytes(transcript)
    digest = _digest_bytes(document_digest)
    total = max(1, (len(transcript) + MAX_PAYLOAD_BYTES - 1) // MAX_PAYLOAD_BYTES)
    if total > 0xFFFFFFFF:
        raise QrTransportError("transcript requires too many QR chunks")
    return [
        pack_chunk(
            QrChunk(
                digest,
                index,
                total,
                transcript[
                    index * MAX_PAYLOAD_BYTES : (index + 1) * MAX_PAYLOAD_BYTES
                ],
            )
        )
        for index in range(total)
    ]


def reassemble_symbols(symbols: _ty.Iterable[bytes]) -> bytes:
    """Validate and reassemble an unordered collection of GQ1 symbols."""
    chunks = [unpack_chunk(symbol) for symbol in symbols]
    if not chunks:
        raise QrTransportError("no Glyphive QR symbols were found")
    expected_digest = chunks[0].document_digest
    expected_total = chunks[0].total
    by_index: _ty.Dict[int, bytes] = {}
    for chunk in chunks:
        if chunk.document_digest != expected_digest:
            raise QrTransportError("QR symbols belong to different documents")
        if chunk.total != expected_total:
            raise QrTransportError("QR symbols disagree on the chunk total")
        if chunk.index in by_index:
            raise QrTransportError(f"duplicate QR chunk index {chunk.index}")
        by_index[chunk.index] = chunk.payload
    missing = [index for index in range(expected_total) if index not in by_index]
    if missing:
        preview = ", ".join(str(index) for index in missing[:8])
        suffix = "..." if len(missing) > 8 else ""
        raise QrTransportError(f"missing QR chunk indices: {preview}{suffix}")
    return b"".join(by_index[index] for index in range(expected_total))


def symbol_png(symbol: bytes, *, scale: int = 4) -> bytes:
    """Render one GQ1 envelope as a level-H, non-Micro QR PNG."""
    unpack_chunk(symbol)
    if scale <= 0:
        raise ValueError("QR scale must be positive")
    try:
        import segno
    except ImportError as exc:  # pragma: no cover - exercised by base-install probe
        raise RuntimeError("QR rendering requires 'pip install glyphive[qr]'") from exc
    qr = segno.make(
        symbol,
        error="h",
        micro=False,
        boost_error=False,
    )
    output = io.BytesIO()
    qr.save(output, kind="png", scale=scale, border=4)
    return output.getvalue()


def symbols_from_image(image: _ty.Any) -> _ty.List[bytes]:
    """Decode all QR symbols in a Pillow image and return uninterpreted bytes."""
    try:
        import zxingcpp
    except ImportError as exc:  # pragma: no cover - exercised by base-install probe
        raise RuntimeError("QR decoding requires 'pip install glyphive[qr]'") from exc
    if getattr(image, "mode", None) not in {"L", "RGB", "RGBA"}:
        image = image.convert("L")
    results = zxingcpp.read_barcodes(
        image,
        formats=zxingcpp.BarcodeFormat.QRCode,
    )
    return [bytes(result.bytes) for result in results]


def transcript_from_images(path: _ty.Union[str, Path]) -> bytes:
    """Decode a page image or sorted direct-child image directory."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - exercised by base-install probe
        raise RuntimeError("QR decoding requires 'pip install glyphive[qr]'") from exc
    source = Path(path)
    paths = sorted(item for item in source.iterdir() if item.is_file()) if source.is_dir() else [source]
    symbols: _ty.List[bytes] = []
    for image_path in paths:
        with image_path.open("rb") as stream:
            prefix = stream.read(16)
        image_magic = (
            prefix.startswith(b"\x89PNG\r\n\x1a\n")
            or prefix.startswith(b"\xff\xd8\xff")
            or prefix.startswith((b"GIF87a", b"GIF89a"))
            or prefix.startswith(b"BM")
            or prefix.startswith((b"II*\x00", b"MM\x00*"))
            or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP")
        )
        if not image_magic:
            raise QrTransportError(
                f"QR input is not a supported page image by magic bytes: {image_path}"
            )
        with Image.open(image_path) as image:
            decoded = symbols_from_image(image)
        if not decoded:
            raise QrTransportError(f"no QR symbols found in {image_path}")
        symbols.extend(decoded)
    return reassemble_symbols(symbols)


__all__ = [
    "MAX_PAYLOAD_BYTES",
    "MAX_SYMBOL_BYTES",
    "QrChunk",
    "QrTransportError",
    "chunk_transcript",
    "pack_chunk",
    "reassemble_symbols",
    "symbol_png",
    "symbols_from_image",
    "transcript_from_images",
    "unpack_chunk",
]
