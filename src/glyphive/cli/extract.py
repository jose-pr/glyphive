"""The ``glyphive extract`` command."""

from __future__ import annotations

import typing as _ty

from duho import LoggingArgs
from pathlib_next import Path

from ._common import (
    load_input_lines_with_conf,
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

    from_qr: bool = False
    "Decode -f as GQ1 QR page images (requires glyphive[qr])."
    ("--from-qr",)

    ocr_engine: "_ty.Optional[str]" = None
    "OCR registry provider for image input (default: automatic preference)."
    ("--ocr-engine",)

    descan: str = "auto"
    "Gaussian de-scan blur for image/PDF input before OCR. 'auto' (the default) "
    "does one sharp pass, then automatically retries once over a light blur "
    "ladder (0.6, 0.8) if decode fails -- raw phone photos are often too "
    "sharp/noisy to read without it, and wider glyphs can need a touch more "
    "blur; the retry costs extra OCR passes only on failure. '0' "
    "disables the auto-retry (single no-blur pass). An explicit list (e.g. "
    "'0,0.6,1.0') OCRs each image at every radius and merges the CRC-valid lines "
    "across passes -- different blurs recover different lines, and the per-line "
    "CRC makes combining them safe -- with no additional auto-retry. Ignored for "
    "text transcripts and DOCX."
    ("--descan",)

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
        from ._common import resolve_descan

        dest = resolve_destination(self.directory)
        src = Path(self.file)
        blur_radii, auto_retry = resolve_descan(self.descan)

        def _load(radii, *, spine=None):
            # Returns (lines, char_conf). char_conf is per-line OCR character
            # confidence (aligned to lines by physical order) or None for a line
            # that had no OCR (QR / text / DOCX). The codec uses it only to
            # narrow a CRC-failed line's erasures; correctness still rests on
            # CRC/RS/SHA, so a None confidence is exactly today's behavior.
            #
            # ``spine`` (the already-computed sharp pass) is only meaningful
            # for the OCR loaders -- QR decode has no blur ladder to retry.
            if self.from_qr:
                return load_qr_lines(src), None
            return load_input_lines_with_conf(
                src, engine=self.ocr_engine, blur=radii, spine=spine
            )

        lines, char_conf = _load(blur_radii)
        meta, written = self._restore_with_descan_retry(
            _unarchive, dest, lines, char_conf, _load, auto_retry, src
        )
        warn_page_integrity(self._logger_, meta)
        self._logger_.info("restored %d entries into %s", len(written), dest)
        return 0

    def _restore_with_descan_retry(
        self, _unarchive, dest, lines, char_conf, load_fn, auto_retry, src
    ):
        """Restore; on a too-sharp-photo decode failure, auto-retry with a blur.

        The cross-pass OCR merge only ADDS CRC-valid lines, so a blurred retry
        can never corrupt a transcript that would already decode -- it can only
        recover more. Retry is limited to one extra sweep over the blur ladder
        (AUTO_DESCAN_RETRY_RADII) and only when the input is entirely image/PDF
        and the user did not pass an explicit --descan. The already-computed
        sharp (0.0) ``lines`` are passed back in as the retry's ``spine`` so
        that pass is never re-OCR'd -- only the extra radii are.
        """
        from .. import layout as _layout
        from ..codec.engine import CodecError
        from ._common import AUTO_DESCAN_RETRY_RADII, input_is_image_or_pdf

        retryable = (_layout.LayoutError, CodecError)
        try:
            return self._restore(_unarchive, dest, lines, char_conf)
        except retryable as first_error:
            if not (auto_retry and not self.from_qr and input_is_image_or_pdf(src)):
                raise
            self._logger_.warning(
                "restore failed on the sharp pass (%s); retrying over the light "
                "de-scan blur ladder %s", type(first_error).__name__,
                AUTO_DESCAN_RETRY_RADII,
            )
            from ..restore.ocr import OcrLine

            sharp_confs = char_conf if char_conf is not None else [None] * len(lines)
            spine = [OcrLine(text, conf) for text, conf in zip(lines, sharp_confs)]
            retry_lines, retry_conf = load_fn(AUTO_DESCAN_RETRY_RADII, spine=spine)
            try:
                return self._restore(_unarchive, dest, retry_lines, retry_conf)
            except retryable as retry_error:
                self._logger_.debug(
                    "de-scan blur retry did not recover the document (%s); "
                    "re-raising the original sharp-pass error",
                    type(retry_error).__name__,
                )
                raise first_error

    def _restore(self, _unarchive, dest, lines, char_conf=None):
        return _unarchive.restore_document_spooled(
            lines,
            dest,
            overwrite=self.overwrite,
            temp_dir=self.temp_dir,
            chunk_size=self.chunk_size,
            max_output_bytes=self.max_output_bytes,
            on_progress=progress_logger(self._logger_),
            char_conf=char_conf,
        )
