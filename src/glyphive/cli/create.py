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
from ._common import format_selector_error, resolve_destination

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

    line_width: "_ty.Optional[int]" = None
    "Codec payload characters per row (default: largest measured PDF fit)."
    ("--line-width",)

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
        if self.line_width is not None:
            if self.line_width < 2:
                raise SystemExit("error: --line-width must be at least 2")
            if measured_capacity is not None and self.line_width > measured_capacity:
                raise SystemExit(
                    "error: --line-width %d exceeds the measured PDF capacity %d"
                    % (self.line_width, measured_capacity)
                )
            line_width = self.line_width
        else:
            line_width = measured_capacity or 60
        out = Path(self.file)
        with _tempfile.TemporaryFile(dir=self.temp_dir) as raw_spool:
            measured_raw = _DigestWriter(raw_spool)
            _archive.write_archive(
                root,
                measured_raw,
                use_ignore=not self.no_ignore,
                metadata=self.metadata,
                chunk_size=self.chunk_size,
            )
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
                compressed_spool.seek(0)
                if hasattr(codec, "iter_encode") and codec_name == "base16c-crc16-rs":
                    encoded = codec.iter_encode(
                        compressed_spool,
                        compressed_len,
                        line_width=line_width,
                        temp_dir=self.temp_dir,
                    )
                    n_encoded = _base16c_encoded_line_count(
                        compressed_len, line_width=line_width
                    )
                else:
                    options = {"line_width": line_width} if codec_name == "base16c-crc16-rs" else {}
                    materialized = codec.encode(compressed_spool.read(), **options)
                    encoded, n_encoded = iter(materialized), len(materialized)
                pages = _layout.iter_paginate(
                    encoded,
                    n_encoded,
                    meta,
                    lines_per_page=lines_per_page,
                )
                renderer.render(
                    pages,
                    out,
                    font=self.font,
                    font_size=self.font_size,
                    page_margin_pt=page_margin_pt,
                    horizontal_alignment=self.horizontal_alignment,
                    character_spacing_pt=self.character_spacing,
                )
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
