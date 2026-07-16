"""Optional zstandard compression method with lazy backend imports."""

from __future__ import annotations

import typing as _ty

from ._base import CompressionMethod


def _zstd_module():
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise RuntimeError(
            "zstd compression requires the optional 'zstandard' dependency; "
            "install it with: pip install glyphive[zstd]"
        ) from exc
    return zstandard


def _available_zstd_module():
    """Load zstandard and add the project extra hint to backend failures."""
    try:
        return _zstd_module()
    except RuntimeError as exc:
        message = str(exc)
        if "pip install glyphive[zstd]" not in message:
            message += "; install it with: pip install glyphive[zstd]"
        raise RuntimeError(message) from exc


class ZstdCompression(CompressionMethod):
    name = "zstd"

    @classmethod
    def is_available(cls) -> bool:
        try:
            _zstd_module()
        except RuntimeError:
            return False
        return True

    def compress(self, data: bytes, level: _ty.Optional[int] = None) -> bytes:
        zstandard = _available_zstd_module()
        return zstandard.ZstdCompressor(level=3 if level is None else level).compress(data)

    def decompress(self, data: bytes) -> bytes:
        return _available_zstd_module().ZstdDecompressor().decompress(data)
