"""The ``glyphive create`` command."""

from __future__ import annotations

import hashlib as _hashlib
import typing as _ty

from duho import NS, LoggingArgs
from pathlib_next import Path

from .. import archive as _archive
from .. import codec as _codec
from .. import compression as _compression
from .. import layout as _layout
from .. import render as _render
from ._common import format_selector_error, resolve_destination

__all__ = ["Create"]


_FORMAT_EXTRAS = {"pdf": "glyphive[pdf]", "docx": "glyphive[docx]"}
_COMPRESSION_EXTRAS = {"zstd": "glyphive[zstd]"}


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

    codec: str = "g1"
    "Printable codec name (default: g1)."
    ("--codec",)

    metadata: "_ty.Literal['none', 'basic']" = "none"
    "Archive metadata profile."
    ("--metadata",)

    compression: "_ty.Optional[str]" = None
    "Compression registry name; default selects the registry default."
    ("--compression",)

    format: "str" = "text"
    "Output render format registry name (default: text)."
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
        renderer = _select_renderer(self.format)

        base = resolve_destination(self.directory)
        roots = [base / path for path in self.paths]
        if len(roots) != 1:
            raise SystemExit(
                "error: v1 archives a single path (a directory or '.'); "
                "got %d — wrap multiple inputs in a directory" % len(roots)
            )
        root = roots[0]

        raw = _archive.archive_tree(
            root,
            use_ignore=not self.no_ignore,
            metadata=self.metadata,
        )
        paths = _archive.list_paths(root, use_ignore=not self.no_ignore)
        payload = compression.compress(raw, self.level)
        encoded = codec.encode(payload)

        meta = {
            "v": 1,
            "codec": codec_name,
            "comp": compression_name,
            "meta": self.metadata,
            "files": len(paths),
            "bytes": len(raw),
            "sha256": _hashlib.sha256(raw).hexdigest(),
        }
        page_margin_pt = (
            _render.MINIMAL_PAGE_MARGIN_PT
            if self.minimal_margins
            else _render.DEFAULT_PAGE_MARGIN_PT
        )
        lines_per_page = _render.lines_per_page_for(
            self.font_size, page_margin_pt=page_margin_pt
        )
        pages = _layout.paginate(encoded, meta, lines_per_page=lines_per_page)

        out = Path(self.file)
        renderer.render(
            pages,
            out,
            font=self.font,
            font_size=self.font_size,
            page_margin_pt=page_margin_pt,
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
            self.format,
        )
        return 0
