"""EasyOCR provider with lazy third-party imports."""

from __future__ import annotations

import importlib.util

from .._base import OcrProvider
from ._image import load_image


class EasyOcrProvider(OcrProvider):
    name = "easyocr"

    @classmethod
    def is_available(cls) -> bool:
        try:
            return importlib.util.find_spec("easyocr") is not None
        except Exception:
            return False

    def ocr_image(self, image_path) -> list[str]:
        import numpy as np
        import easyocr

        reader = easyocr.Reader(["en"], gpu=False)
        return [
            str(text)
            for text in reader.readtext(
                np.array(load_image(image_path)), detail=0, paragraph=False
            )
            if text
        ]
