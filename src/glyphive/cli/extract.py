"""The ``glyphive extract`` command."""

from __future__ import annotations

import typing as _ty

from duho import LoggingArgs
from pathlib_next import Path

from ._common import (
    load_image_lines,
    load_input_lines,
    load_qr_lines,
    progress_logger,
    resolve_destination,
    warn_page_integrity,
)

__all__ = ["Extract"]


class Extract(LoggingArgs):
    """Restore a directory tree from a glyphive document."""

    _parsername_ = "extract"
    _parseraliases_ = ["x"]

    file: str
    "Input file or directory (text, images, PDF, or DOCX; type detected by extension)."
    ("-f", "--file")

    directory: "_ty.Optional[str]" = None
    "Restore into this directory (tar -C). Default: current directory."
    ("-C", "--directory")

    from_images: bool = False
    "Treat -f as a page image or directory of images and OCR them first."
    ("--from-images",)

    from_qr: bool = False
    "Decode -f as GQ1 QR page images (requires glyphive[qr])."
    ("--from-qr",)

    ocr_engine: "_ty.Optional[str]" = None
    "OCR registry provider for image input (default: automatic preference)."
    ("--ocr-engine",)

    overwrite: bool = False
    "Overwrite existing files that differ (default: refuse and stop)."
    ("--overwrite",)

    temp_dir: "_ty.Optional[str]" = None
    "Directory for private restore spools."
    ("--temp-dir",)

    chunk_size: int = 1024 * 1024
    "Streaming I/O chunk size in bytes."
    ("--chunk-size",)

    max_output_bytes: "_ty.Optional[int]" = None
    "Maximum permitted decompressed archive size."
    ("--max-output-bytes",)

    def __call__(self) -> int:
        from ..restore import unarchive as _unarchive

        dest = resolve_destination(self.directory)
        src = Path(self.file)
        if self.from_images and self.from_qr:
            raise ValueError("--from-images and --from-qr are mutually exclusive")
        if self.from_qr:
            lines = load_qr_lines(src)
        elif self.from_images:
            lines = load_image_lines(src, engine=self.ocr_engine)
        else:
            lines = load_input_lines(src, engine=self.ocr_engine)

        meta, written = _unarchive.restore_document_spooled(
            lines,
            dest,
            overwrite=self.overwrite,
            temp_dir=self.temp_dir,
            chunk_size=self.chunk_size,
            max_output_bytes=self.max_output_bytes,
            on_progress=progress_logger(self._logger_),
        )
        warn_page_integrity(self._logger_, meta)
        self._logger_.info("restored %d entries into %s", len(written), dest)
        return 0
