"""PaddleOCR provider with lazy third-party imports."""

from __future__ import annotations

import importlib.util
import typing as _ty

from .._base import OcrLine, OcrProvider
from ._image import load_image


class PaddleProvider(OcrProvider):
    name = "paddle"

    @classmethod
    def is_available(cls) -> bool:
        try:
            return importlib.util.find_spec("paddleocr") is not None
        except Exception:
            return False

    def ocr_image(self, image_path) -> _ty.List[OcrLine]:
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
        lines: _ty.List[OcrLine] = []
        for result in ocr.predict(arr):
            try:
                texts = result.get("rec_texts", [])
                scores = result.get("rec_scores", [])
            except AttributeError:
                texts, scores = [], []
            # ``rec_scores`` is the parallel per-LINE confidence array Paddle
            # already computes and the prior implementation discarded; only
            # line-level (not per-character) granularity is available, so
            # broadcast each line's own score across its characters.
            for index, text in enumerate(texts):
                if not text:
                    continue
                text = str(text)
                score = float(scores[index]) if index < len(scores) else None
                conf = [score] * len(text) if score is not None else None
                lines.append(OcrLine(text, conf))
        return lines
