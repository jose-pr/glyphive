"""The ``glyphive list`` command."""

from __future__ import annotations

import typing as _ty
import tempfile as _tempfile

from duho import LoggingArgs
from pathlib_next import Path

from .. import archive as _archive
from ._common import load_input_lines

__all__ = ["List"]


class List(LoggingArgs):
    """Print a document's header and file manifest without extracting."""

    _parsername_ = "list"
    _parseraliases_ = ["t"]

    file: str
    "Input file or directory (text, images, PDF, or DOCX; type detected automatically)."
    ("-f", "--file")

    ocr_engine: "_ty.Optional[str]" = None
    "OCR registry provider for image or document input (default: automatic preference)."
    ("--ocr-engine",)

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
        from ..restore import decode as _decode

        lines = load_input_lines(Path(self.file), engine=self.ocr_engine)
        # Decode first so every displayed field comes from the integrity-
        # protected H frames, never from the unrestricted human summary.
        with _tempfile.TemporaryFile(dir=self.temp_dir) as raw:
            header = _decode.decode_document_to_spool(
                lines,
                raw,
                max_output_bytes=self.max_output_bytes,
                chunk_size=self.chunk_size,
            )
            profile = header.get("meta")
            profile_token = f" meta={profile}" if profile is not None else ""
            print(
                "glyphive v{v} codec={codec} comp={comp}{profile} files={files} "
                "bytes={bytes} pages={pages}".format(
                    **header, profile=profile_token
                )
            )
            raw.seek(0)
            for event in _archive.iter_record_events(raw, chunk_size=self.chunk_size):
                if isinstance(event, _archive.RecordHeader):
                    kind = "d" if event.type == _archive.REC_EMPTY_DIR else "f"
                    print(f"{kind} {event.path}")
        return 0
