"""EasyOCR provider with lazy third-party imports."""

from __future__ import annotations

import importlib.util
import typing as _ty

from .._base import OcrLine, OcrProvider
from ._image import load_image


class EasyOcrProvider(OcrProvider):
    name = "easyocr"

    @classmethod
    def is_available(cls) -> bool:
        try:
            return importlib.util.find_spec("easyocr") is not None
        except Exception:
            return False

    def ocr_image(self, image_path) -> _ty.List[OcrLine]:
        import numpy as np
        import easyocr

        reader = easyocr.Reader(["en"], gpu=False)
        lines: _ty.List[OcrLine] = []
        # ``detail=1`` restores the ``(bbox, text, confidence)`` tuples
        # ``detail=0`` throws away; EasyOCR only scores whole detected
        # SEGMENTS (roughly a line/word run), not individual characters, so
        # that one confidence is broadcast uniformly across every character
        # of the segment's text -- a coarser hint than Tesseract's per-word
        # granularity, but still strictly better than discarding it.
        for _bbox, text, confidence in reader.readtext(
            np.array(load_image(image_path)), detail=1, paragraph=False
        ):
            text = str(text)
            if not text:
                continue
            lines.append(OcrLine(text, [float(confidence)] * len(text)))
        return lines
