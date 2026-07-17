"""The ``glyphive extract`` command."""

from __future__ import annotations

import typing as _ty

from duho import LoggingArgs
from pathlib_next import Path

from ._common import (
    load_image_lines,
    load_transcript_lines,
    resolve_destination,
    warn_page_integrity,
)

__all__ = ["Extract"]


class Extract(LoggingArgs):
    """Restore a directory tree from a glyphive document."""

    _parsername_ = "extract"
    _parseraliases_ = ["x"]

    file: str
    "Input file or directory of files (text, or images with --from-images)."
    ("-f", "--file")

    directory: "_ty.Optional[str]" = None
    "Restore into this directory (tar -C). Default: current directory."
    ("-C", "--directory")

    from_images: bool = False
    "Treat -f as a page image or directory of images and OCR them first."
    ("--from-images",)

    ocr_engine: "_ty.Optional[str]" = None
    "OCR registry provider for image input (default: automatic preference)."
    ("--ocr-engine",)

    overwrite: bool = False
    "Overwrite existing files that differ (default: refuse and stop)."
    ("--overwrite",)

    def __call__(self) -> int:
        from ..restore import decode as _decode
        from ..restore import unarchive as _unarchive

        dest = resolve_destination(self.directory)
        src = Path(self.file)
        if self.from_images:
            lines = load_image_lines(src, engine=self.ocr_engine)
        else:
            lines = load_transcript_lines(src)

        meta, raw = _decode.decode_document(lines)
        warn_page_integrity(self._logger_, meta)
        written = _unarchive.unarchive_bytes(raw, dest, overwrite=self.overwrite)
        self._logger_.info("restored %d entries into %s", len(written), dest)
        return 0
