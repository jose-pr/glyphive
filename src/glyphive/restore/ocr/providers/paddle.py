"""PaddleOCR provider with lazy third-party imports."""

from __future__ import annotations

import importlib.util

from .._base import OcrProvider
from ._image import load_image


class PaddleProvider(OcrProvider):
    name = "paddle"

    @classmethod
    def is_available(cls) -> bool:
        try:
            return importlib.util.find_spec("paddleocr") is not None
        except Exception:
            return False

    def ocr_image(self, image_path) -> list[str]:
        import numpy as np
        from paddleocr import PaddleOCR

        arr = np.array(load_image(image_path))
        ocr = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang="en",
            enable_mkldnn=False,
        )
        lines = []
        for result in ocr.predict(arr):
            try:
                texts = result.get("rec_texts", [])
            except AttributeError:
                texts = []
            lines.extend(str(text) for text in texts if text)
        return lines
