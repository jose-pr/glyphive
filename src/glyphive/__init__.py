"""glyphive — archive file trees to compact, OCR-friendly printable pages.

Take a directory tree (or arbitrary bytes), serialize it, compress it, and encode
it into an OCR-safe, human-re-typeable printable format laid out on pages (plain
text, PDF, or Word), then restore the tree from a scan or a re-typed transcript.

The default codec (``base16g-crc16-rs``) uses the measured-safe 16-character alphabet
``ABCDHKLMPRTVXY34``, a per-line CRC-16, and document-wide interleaved
Reed-Solomon parity so scattered OCR errors self-heal instead of silently
corrupting everything downstream.
"""

__all__ = ["__version__"]

try:  # pragma: no cover - trivial metadata plumbing
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("glyphive")
    except PackageNotFoundError:
        __version__ = "0.0.0+unknown"
except Exception:  # pragma: no cover
    __version__ = "0.0.0+unknown"
