"""The ``glyphive create`` command."""

from __future__ import annotations

import hashlib as _hashlib
import tempfile as _tempfile
import typing as _ty

from duho import NS, LoggingArgs
from pathlib_next import Path

from .. import archive as _archive
from .. import codec as _codec
from .. import compression as _compression
from .. import layout as _layout
from .. import render as _render
from ..codec.base16c import encoded_line_count as _base16c_encoded_line_count
from ._common import format_selector_error, progress_logger, resolve_destination

__all__ = ["Create"]


_FORMAT_EXTRAS = {
    "pdf": "glyphive[pdf]",
    "docx": "glyphive[docx]",
    "qr": "glyphive[qr,pdf]",
    "hybrid": "glyphive[qr,pdf]",
}
_COMPRESSION_EXTRAS = {"zstd": "glyphive[zstd]"}
_FORMAT_BY_SUFFIX = {
    ".txt": "text",
    ".text": "text",
    ".pdf": "pdf",
    ".docx": "docx",
}


class _DigestWriter:
    def __init__(self, sink):
        self.sink = sink
        self.digest = _hashlib.sha256()
        self.count = 0

    def write(self, data):
        written = self.sink.write(data)
        if written != len(data):
            raise OSError("temporary archive spool accepted a partial write")
        self.digest.update(data)
        self.count += len(data)
        return written


def _output_format(destination: str, explicit: _ty.Optional[str]) -> str:
    """Return an explicit format or infer a known format from the output suffix."""
    if explicit is not None:
        return explicit
    return _FORMAT_BY_SUFFIX.get(Path(destination).suffix.lower(), "text")


def _select_codec(name: str):
    names = _codec.names()
    if name not in names or name not in _codec.available():
        message = format_selector_error(
            "codec", name, names, available=_codec.available()
        )
        raise ValueError(message)
    return _codec.get(name)


def _select_compression(name: str):
    names = _compression.names()
    available = _compression.available()
    if name not in names or name not in available:
        message = format_selector_error(
            "compression method",
            name,
            names,
            available=available,
            extra=_COMPRESSION_EXTRAS.get(name),
        )
        raise ValueError(message)
    return _compression.get(name)


def _select_renderer(name: str):
    names = _render.names()
    available = _render.available()
    if name not in names or name not in available:
        message = format_selector_error(
            "render format",
            name,
            names,
            available=available,
            extra=_FORMAT_EXTRAS.get(name),
        )
        raise ValueError(message)
    return _render.get(name)


class Create(LoggingArgs):
    """Archive PATHS into an OCR-friendly printable document."""

    _parsername_ = "create"
    _parseraliases_ = ["c"]

    file: str
    "Output document path (- writes text to stdout is not supported; give a path)."
    ("-f", "--file")

    paths: "list[str]"
    "One or more files/directories to archive (relative to -C if given)."
    ("paths",)

    directory: "_ty.Optional[str]" = None
    "Change to this directory before reading PATHS (tar -C)."
    ("-C", "--directory")

    codec: str = "base16c-crc16-rs"
    "Printable codec name (default: base16c-crc16-rs)."
    ("--codec",)

    metadata: "_ty.Literal['none', 'basic']" = "none"
    "Archive metadata profile."
    ("--metadata",)

    compression: "_ty.Optional[str]" = None
    "Compression registry name; default selects the registry default."
    ("--compression",)

    format: "_ty.Optional[str]" = None
    "Output format name; QR/hybrid must be explicit because both write PDF."
    ("--format",)

    gzip: "_ty.Annotated[bool, NS(conflicts='legacy_compression')]" = False
    "Compress with gzip."
    ("-z", "--gzip")

    zstd: "_ty.Annotated[bool, NS(conflicts='legacy_compression')]" = False
    "Compress with zstd (needs glyphive[zstd])."
    ("--zstd",)

    none: "_ty.Annotated[bool, NS(conflicts='legacy_compression')]" = False
    "Do not compress."
    ("--none",)

    level: "_ty.Optional[int]" = None
    "Compression level (0-9)."
    ("-L", "--level")

    no_ignore: bool = False
    "Do not honor .gitignore/.ignore files (archive everything)."
    ("--no-ignore",)

    font: "_ty.Optional[str]" = None
    "Font family for pdf/docx output (default: an OCR-friendly monospace)."
    ("--font",)

    font_size: float = 11.0
    "Font size in points for pdf/docx output."
    ("--font-size",)

    minimal_margins: bool = False
    "Use compact 12-point page margins for denser pdf/docx output."
    ("--minimal-margins",)

    horizontal_alignment: "_ty.Literal['left', 'center', 'justify']" = "left"
    "Horizontal line alignment for pdf/docx output."
    ("--horizontal-alignment",)

    character_spacing: float = 0.0
    "Extra space between characters in points for pdf/docx output."
    ("--character-spacing",)

    line_width: "_ty.Optional[str]" = None
    "Codec payload characters per row: 'auto' (OCR-measured-safe cap, ≤ the "
    "60-char safe width; the default), 'max' (largest row that physically fits "
    "the font/size/margins — may exceed 60 and is NOT OCR-verified), or an "
    "explicit integer ≥ 2. An integer above the safe cap needs --force."
    ("--line-width",)

    force: bool = False
    "Allow an explicit --line-width above the OCR-measured-safe cap (up to the "
    "geometric fit). Ignored unless --line-width is an integer past the safe "
    "width. 'max' selects the geometric fit directly and needs no --force."
    ("--force",)

    parity_ratio: float = 0.12
    "Reed-Solomon parity as a fraction of protected bytes (default 0.12 = "
    "12%). Lower values shrink page count but reduce how much scan/OCR "
    "damage a document can self-heal; 0 is not allowed, see --simple for a "
    "documented low-redundancy preset instead of hand-tuning this."
    ("--parity-ratio",)

    parity_pages: int = 0
    "Whole-page recovery: emit K extra pages carrying document-level "
    "Reed-Solomon parity over the data pages (default 0 = off, matches prior "
    "behavior exactly). Survives up to K wholly lost/unscannable data pages, "
    "independent of --parity-ratio's per-line correction; costs K extra "
    "printed pages. Data pages + K must not exceed 255 (a create-time error "
    "names the cap if exceeded)."
    ("--parity-pages",)

    simple: bool = False
    "Low-redundancy preset for small, disposable, or re-typeable documents: "
    "parity_ratio 0.04 instead of 0.12 (roughly a third of the default "
    "parity overhead). Trades most of the RS self-healing budget for fewer "
    "pages; still protected by per-line CRC (bad lines are still detected, "
    "just less likely to be automatically corrected) and the whole-document "
    "SHA-256 gate (a corrupted restore is never accepted as silently "
    "correct). Not for documents you can't easily rescan or retype on failure."
    ("--simple",)

    temp_dir: "_ty.Optional[str]" = None
    "Directory for bounded-memory spool files (default: system temporary directory)."
    ("--temp-dir",)

    chunk_size: int = 1024 * 1024
    "Streaming I/O chunk size in bytes."
    ("--chunk-size",)

    def _legacy_compression(self) -> _ty.Optional[str]:
        for name, selected in (
            ("gzip", self.gzip),
            ("zstd", self.zstd),
            ("none", self.none),
        ):
            if selected:
                return name
        return None

    def _compression_selection(self) -> _ty.Tuple[str, _ty.Any]:
        legacy = self._legacy_compression()
        if self.compression and legacy and self.compression != legacy:
            raise SystemExit(
                "error: --compression %s disagrees with --%s"
                % (self.compression, legacy)
            )
        name = self.compression or legacy or _compression.default()
        return name, _select_compression(name)

    _SIMPLE_PARITY_RATIO = 0.04

    def _parity_ratio_selection(self) -> float:
        if self.simple and self.parity_ratio != 0.12:
            raise SystemExit(
                "error: --simple disagrees with an explicit --parity-ratio "
                "%.4g; pass only one" % self.parity_ratio
            )
        if self.simple:
            return self._SIMPLE_PARITY_RATIO
        if not 0 < self.parity_ratio < 1:
            raise SystemExit("error: --parity-ratio must be in (0, 1)")
        return self.parity_ratio

    def _resolve_line_width(
        self, renderer, measured_capacity, page_margin_pt
    ) -> int:
        """Resolve --line-width auto|max|<int> to a concrete payload width.

        ``auto`` (or omitted) = the OCR-measured-safe capacity (≤60). ``max`` =
        the renderer's uncapped geometric fit (may exceed 60, unmeasured), or a
        hard error on a format with no geometric metrics. An integer above the
        safe cap needs ``--force`` and must still fit the geometric width.
        """
        raw = self.line_width
        if raw is None or raw == "auto":
            return measured_capacity or 60
        if raw == "max":
            geometric = renderer.geometric_payload_capacity(
                font=self.font,
                font_size=self.font_size,
                page_margin_pt=page_margin_pt,
                character_spacing_pt=self.character_spacing,
            )
            if geometric is None:
                raise SystemExit(
                    "error: --line-width max needs a format with physical font "
                    "metrics (PDF); use 'auto' or an explicit integer for "
                    "text/docx/qr output"
                )
            return geometric
        try:
            width = int(raw)
        except ValueError:
            raise SystemExit(
                "error: --line-width must be 'auto', 'max', or an integer, "
                "got %r" % raw
            ) from None
        if width < 2:
            raise SystemExit("error: --line-width must be at least 2")
        safe_cap = measured_capacity  # None on non-PDF (no cap enforced)
        if safe_cap is not None and width > safe_cap:
            if not self.force:
                raise SystemExit(
                    "error: --line-width %d exceeds the OCR-measured-safe "
                    "capacity %d; pass --force to use an unmeasured width up to "
                    "the geometric fit, or --line-width max" % (width, safe_cap)
                )
            geometric = renderer.geometric_payload_capacity(
                font=self.font,
                font_size=self.font_size,
                page_margin_pt=page_margin_pt,
                character_spacing_pt=self.character_spacing,
            )
            if geometric is not None and width > geometric:
                raise SystemExit(
                    "error: --line-width %d exceeds even the geometric fit %d "
                    "(it would overflow the page)" % (width, geometric)
                )
        return width

    def __call__(self) -> int:
        codec_name = self.codec
        codec = _select_codec(codec_name)
        compression_name, compression = self._compression_selection()
        format_name = _output_format(self.file, self.format)
        renderer = _select_renderer(format_name)

        base = resolve_destination(self.directory)
        roots = [base / path for path in self.paths]
        if len(roots) != 1:
            raise SystemExit(
                "error: v1 archives a single path (a directory or '.'); "
                "got %d — wrap multiple inputs in a directory" % len(roots)
            )
        root = roots[0]

        paths = _archive.list_paths(root, use_ignore=not self.no_ignore)
        if self.chunk_size <= 0:
            raise SystemExit("error: --chunk-size must be a positive integer")
        page_margin_pt = (
            _render.MINIMAL_PAGE_MARGIN_PT
            if self.minimal_margins
            else _render.DEFAULT_PAGE_MARGIN_PT
        )
        lines_per_page = _render.lines_per_page_for(
            self.font_size, page_margin_pt=page_margin_pt
        )
        measured_capacity = renderer.payload_capacity(
            font=self.font,
            font_size=self.font_size,
            page_margin_pt=page_margin_pt,
            character_spacing_pt=self.character_spacing,
        )
        if measured_capacity is not None and measured_capacity < 60:
            raise SystemExit(
                "error: selected PDF geometry fits only %d payload characters; "
                "at least 60 are required for protected header/footer frames"
                % measured_capacity
            )
        line_width = self._resolve_line_width(
            renderer, measured_capacity, page_margin_pt
        )
        out = Path(self.file)
        report = progress_logger(self._logger_)
        with _tempfile.TemporaryFile(dir=self.temp_dir) as raw_spool:
            measured_raw = _DigestWriter(raw_spool)
            _archive.write_archive(
                root,
                measured_raw,
                use_ignore=not self.no_ignore,
                metadata=self.metadata,
                chunk_size=self.chunk_size,
            )
            report("archived", files=len(paths), bytes=measured_raw.count)
            meta = {
                "v": 1,
                "codec": codec_name,
                "comp": compression_name,
                "meta": self.metadata,
                "files": len(paths),
                "bytes": measured_raw.count,
                "sha256": measured_raw.digest.hexdigest(),
            }
            raw_spool.seek(0)
            with _tempfile.TemporaryFile(dir=self.temp_dir) as compressed_spool:
                compression.compress_stream(
                    raw_spool,
                    compressed_spool,
                    level=self.level,
                    chunk_size=self.chunk_size,
                )
                compressed_len = compressed_spool.tell()
                report("compressed", bytes=compressed_len, method=compression_name)
                compressed_spool.seek(0)
                parity_ratio = self._parity_ratio_selection()
                if hasattr(codec, "iter_encode") and codec_name == "base16c-crc16-rs":
                    encoded = codec.iter_encode(
                        compressed_spool,
                        compressed_len,
                        line_width=line_width,
                        parity_ratio=parity_ratio,
                        temp_dir=self.temp_dir,
                    )
                    n_encoded = _base16c_encoded_line_count(
                        compressed_len, line_width=line_width, parity_ratio=parity_ratio
                    )
                else:
                    options = (
                        {"line_width": line_width, "parity_ratio": parity_ratio}
                        if codec_name == "base16c-crc16-rs"
                        else {}
                    )
                    materialized = codec.encode(compressed_spool.read(), **options)
                    encoded, n_encoded = iter(materialized), len(materialized)
                report("encoded", lines=n_encoded, parity_ratio=parity_ratio)
                if self.parity_pages < 0:
                    raise SystemExit("error: --parity-pages must be >= 0")
                try:
                    pages = _layout.iter_paginate(
                        encoded,
                        n_encoded,
                        meta,
                        lines_per_page=lines_per_page,
                        parity_pages=self.parity_pages,
                    )
                    if self.parity_pages:
                        pages = list(pages)
                except _layout.LayoutError as exc:
                    if "Reed-Solomon limit" in str(exc):
                        raise SystemExit(f"error: {exc}") from None
                    raise
                renderer.render(
                    pages,
                    out,
                    font=self.font,
                    font_size=self.font_size,
                    page_margin_pt=page_margin_pt,
                    horizontal_alignment=self.horizontal_alignment,
                    character_spacing_pt=self.character_spacing,
                )
                report("rendered", pages=meta["pages"], format=format_name)
        self._logger_.info(
            "wrote %s (%d files, %d bytes, codec=%s, comp=%s, meta=%s, "
            "%d pages, format=%s)",
            out,
            meta["files"],
            meta["bytes"],
            codec_name,
            compression_name,
            self.metadata,
            meta["pages"],
            format_name,
        )
        return 0
