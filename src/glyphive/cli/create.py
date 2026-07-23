"""The ``glyphive create`` command."""

from __future__ import annotations

import hashlib as _hashlib
import inspect as _inspect
import tempfile as _tempfile
import typing as _ty

from duho import NS, LoggingArgs
from pathlib_next import Path

from .. import archive as _archive
from .. import codec as _codec
from .. import compression as _compression
from .. import layout as _layout
from .. import render as _render
from ..codec.engine import encoded_line_count as _encoded_line_count
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

    codec: str = "base16g-crc16-rs"
    "Printable codec name (default: base16g-crc16-rs)."
    ("--codec",)

    mode: "_ty.Literal['conservative', 'standard', 'max']" = "standard"
    "Measured codec/font/size/width/margin preset (real create->rasterize->"
    "OCR->extract->diff restore gates, see benchmarks/results/FONT_CANDIDATES.md "
    "'Local font/size sweep' and 'Blur-tolerance stress test', 2026-07-23): "
    "'conservative' = base16g-crc16-rs, dejavu-sans-mono, 8pt, --line-width auto "
    "(OCR-measured-safe cap, <=60), regular margins -- lowest density, matches "
    "this project's oldest verified-safe baseline. 'standard' (the default) = "
    "base16g-crc16-rs, dejavu-sans-mono, 6pt, --line-width max, regular margins "
    "-- the most blur-tolerant combination measured (survives a real blur ladder "
    "up to radius 1.5 on both engines, beating Courier and Consolas). 'max' = "
    "the same codec/font/size/width as 'standard' but --minimal-margins for the "
    "smallest page count -- same measured OCR robustness, less paper. Any of "
    "--codec/--font/--font-size/--line-width/--minimal-margins passed "
    "explicitly overrides just that one field from the mode's preset."
    ("--mode",)

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

    no_header: bool = False
    "Omit the display-only '#!glyphive' summary line from page 1. Restore does "
    "not need it (metadata comes from the protected header frames); this yields "
    "the tightest page. The line is emitted by default for human readability."
    ("--no-header",)

    font: "_ty.Optional[str]" = None
    "Font family for pdf/docx output (PDF default: bundled dejavu-sans-mono; DOCX default: "
    "Consolas). PDF also accepts an installed system font's name (by filename or true "
    "font-family name, e.g. Consolas) or an explicit .ttf/.otf path."
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

    line_parity: int = 2
    "Per-line Reed-Solomon parity bytes: 0, 2, or 4 (default 2). Each printed "
    "line carries this many extra parity bytes so many single/double-character "
    "OCR errors self-heal in place, without ever touching the document-level "
    "--parity-ratio budget. 0 disables the field entirely (smallest page, no "
    "in-line self-heal); 4 roughly doubles the per-line correction margin at "
    "~7 more printed characters/line than 2."
    ("--line-parity",)

    parity_pages: int = 0
    "Whole-page recovery: emit K extra pages carrying document-level "
    "Reed-Solomon parity over the data pages (default 0 = off, matches prior "
    "behavior exactly). Survives up to K wholly lost/unscannable data pages, "
    "independent of --parity-ratio's per-line correction; costs K extra "
    "printed pages. Data pages + K use a GF(2^8) field up to 255 total, and "
    "automatically switch to GF(2^16) beyond that (up to 65535 total; a "
    "create-time error names the cap if exceeded)."
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

    #: (codec, font, font_size, line_width, minimal_margins) per --mode.
    #: Measured 2026-07-23 (real create->rasterize->OCR->extract->diff),
    #: see benchmarks/results/FONT_CANDIDATES.md "Local font/size sweep" and
    #: "Blur-tolerance stress test" -- do not hand-edit these numbers without
    #: a matching restore-gate measurement backing the change.
    _MODE_PRESETS = {
        "conservative": ("base16g-crc16-rs", "dejavu-sans-mono", 8.0, "auto", False),
        "standard": ("base16g-crc16-rs", "dejavu-sans-mono", 6.0, "max", False),
        "max": ("base16g-crc16-rs", "dejavu-sans-mono", 6.0, "max", True),
    }

    #: Set True in _apply_mode when line_width came from the mode's preset
    #: rather than an explicit --line-width -- see _resolve_line_width's use
    #: of it to degrade 'max' to 'auto' (instead of erroring) on a format
    #: with no geometric metrics, since the mode is meant to be a sensible
    #: default across every output format, not just PDF.
    _line_width_from_mode = False

    def _apply_mode(self) -> None:
        """Fill in codec/font/font_size/line_width/minimal_margins from
        --mode, for whichever of those fields is still at its own bare
        class default (i.e. was not explicitly passed) -- an explicit flag
        always wins over the mode's preset for that one field.
        """
        codec, font, font_size, line_width, minimal_margins = self._MODE_PRESETS[
            self.mode
        ]
        if self.codec == type(self).codec:
            self.codec = codec
        if self.font is None:
            self.font = font
        if self.font_size == type(self).font_size:
            self.font_size = font_size
        if self.line_width is None:
            self.line_width = line_width
            self._line_width_from_mode = True
        if self.minimal_margins == type(self).minimal_margins:
            self.minimal_margins = minimal_margins

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
        the renderer's uncapped geometric fit (may exceed 60, unmeasured). On
        a format with no geometric metrics (text/docx/qr), an EXPLICIT
        ``--line-width max`` is a hard error naming the limitation; a ``max``
        that came from ``--mode``'s preset (no explicit ``--line-width``)
        instead degrades quietly to ``auto`` -- the mode is meant to be a
        sensible default across every output format, not a PDF-only demand.
        An integer above the safe cap needs ``--force`` and must still fit
        the geometric width.
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
                nsym_line=self.line_parity,
            )
            if geometric is None:
                if self._line_width_from_mode:
                    return measured_capacity or 60
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
                nsym_line=self.line_parity,
            )
            if geometric is not None and width > geometric:
                raise SystemExit(
                    "error: --line-width %d exceeds even the geometric fit %d "
                    "(it would overflow the page)" % (width, geometric)
                )
        return width

    def __call__(self) -> int:
        self._apply_mode()
        codec_name = self.codec
        codec = _select_codec(codec_name)
        if codec_name != "base16g-crc16-rs":
            # Denser codecs pack more bits/char but are not stock-OCR-safe: the
            # measured stock-safe ceiling is base16g's 16 characters. Not a gate
            # (creation never needs OCR) — an informed-choice advisory.
            self._logger_.warning(
                "codec %r is not the OCR-recommended default (base16g-crc16-rs); "
                "denser alphabets need the matching trained OCR model for reliable "
                "scan restore — pick base16g unless you rely on such a model",
                codec_name,
            )
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
        if self.line_parity not in (0, 2, 4):
            raise SystemExit("error: --line-parity must be 0, 2, or 4")
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
            nsym_line=self.line_parity,
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
                "nsym_line": self.line_parity,
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
                # Capability check, not a name check: every radix codec inherits
                # the streaming iter_encode and its exact line-count math, so all
                # of them get the memory-bounded encode and exact page planning.
                # encoded_line_count needs the codec's spec (denser codecs pack
                # more bytes per line), taken from the instance when present.
                codec_spec = getattr(codec, "_spec", None)
                if hasattr(codec, "iter_encode") and codec_spec is not None:
                    encoded = codec.iter_encode(
                        compressed_spool,
                        compressed_len,
                        line_width=line_width,
                        parity_ratio=parity_ratio,
                        nsym_line=self.line_parity,
                        temp_dir=self.temp_dir,
                    )
                    n_encoded = _encoded_line_count(
                        compressed_len,
                        line_width=line_width,
                        parity_ratio=parity_ratio,
                        nsym_line=self.line_parity,
                        spec=codec_spec,
                    )
                else:
                    # A non-radix codec (no shared engine): fall back to the
                    # in-memory encode, passing whatever kwargs it accepts.
                    encode_params = _inspect.signature(codec.encode).parameters
                    options = {
                        name: value
                        for name, value in (
                            ("line_width", line_width),
                            ("parity_ratio", parity_ratio),
                            ("nsym_line", self.line_parity),
                        )
                        if name in encode_params
                    }
                    materialized = codec.encode(compressed_spool.read(), **options)
                    encoded, n_encoded = iter(materialized), len(materialized)
                report(
                    "encoded",
                    lines=n_encoded,
                    parity_ratio=parity_ratio,
                    line_parity=self.line_parity,
                )
                if self.parity_pages < 0:
                    raise SystemExit("error: --parity-pages must be >= 0")
                try:
                    pages = _layout.iter_paginate(
                        encoded,
                        n_encoded,
                        meta,
                        lines_per_page=lines_per_page,
                        parity_pages=self.parity_pages,
                        emit_human_header=not self.no_header,
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
