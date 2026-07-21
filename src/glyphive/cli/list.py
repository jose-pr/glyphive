"""The ``glyphive list`` command."""

from __future__ import annotations

import typing as _ty
import tempfile as _tempfile

from duho import LoggingArgs
from pathlib_next import Path

from .. import archive as _archive
from ._common import load_input_lines, load_qr_lines

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

    descan: str = "auto"
    "De-scan blur for image/PDF input (see `extract --descan`): 'auto' retries "
    "once over a light blur ladder (0.6, 0.8) if the sharp pass fails to decode; "
    "'0' disables it; an explicit list is an OCR sweep merged across radii."
    ("--descan",)

    from_qr: bool = False
    "Decode -f as GQ1 QR page images (requires glyphive[qr])."
    ("--from-qr",)

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
        from ..codec.engine import CodecError
        from .. import layout as _layout
        from ._common import (
            AUTO_DESCAN_RETRY_RADII,
            input_is_image_or_pdf,
            resolve_descan,
        )

        source = Path(self.file)
        blur_radii, auto_retry = resolve_descan(self.descan)

        def _load(radii, *, spine=None):
            return (
                load_qr_lines(source)
                if self.from_qr
                else load_input_lines(
                    source, engine=self.ocr_engine, blur=radii, spine=spine
                )
            )

        def _list(lines):
            # Decode first so every displayed field comes from the integrity-
            # protected H frames, never from the unrestricted human summary.
            with _tempfile.TemporaryFile(dir=self.temp_dir) as raw:
                header = _decode.decode_document_to_spool(
                    lines,
                    raw,
                    max_output_bytes=self.max_output_bytes,
                    chunk_size=self.chunk_size,
                    temp_dir=self.temp_dir,
                )
                self._print_manifest(header, raw)

        lines = _load(blur_radii)
        retryable = (_layout.LayoutError, CodecError)
        try:
            _list(lines)
        except retryable as first_error:
            if not (auto_retry and not self.from_qr and input_is_image_or_pdf(source)):
                raise
            self._logger_.warning(
                "list failed on the sharp pass (%s); retrying over the light "
                "de-scan blur ladder %s", type(first_error).__name__,
                AUTO_DESCAN_RETRY_RADII,
            )
            try:
                _list(_load(AUTO_DESCAN_RETRY_RADII, spine=lines))
            except retryable as retry_error:
                self._logger_.debug(
                    "de-scan blur retry did not recover the document (%s); "
                    "re-raising the original sharp-pass error",
                    type(retry_error).__name__,
                )
                raise first_error
        return 0

    def _print_manifest(self, header, raw) -> None:
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
