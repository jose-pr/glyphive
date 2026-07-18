"""Opt-in glyphive OCR model for OCR-B.

Installing this distribution registers a ``tesseract-glyphive-ocrb`` OCR
provider (via the ``glyphive.ocr_providers`` entry point) that runs Tesseract
against a fine-tuned LSTM model trained on OCR-B renderings of the
glyphive base16g alphabet. On the measured sweep it reads that channel at
0.000% CER (clean and blurred), vs ~4.6% for stock ``eng`` — see the core
repo's ``benchmarks/results/ocr-training-sweep-20260718.json``.

The model is NOT bundled in the core ``glyphive`` wheel; it ships only in this
separate, opt-in package. The trained LSTM ``.traineddata`` is engine-version
independent across the Tesseract 4.x/5.x line (verified).
"""

from __future__ import annotations

from .provider import OcrbGlyphiveProvider

__all__ = ["OcrbGlyphiveProvider"]

#: The font this model targets and the provider name it registers.
FONT = "OCR-B"
PROVIDER_NAME = "tesseract-glyphive-ocrb"
