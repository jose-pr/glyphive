"""Tesseract provider with lazy third-party imports."""

from __future__ import annotations

import importlib.util
import shutil

from .._base import OcrProvider
from ._image import load_image


class TesseractProvider(OcrProvider):
    name = "tesseract"

    @classmethod
    def is_available(cls) -> bool:
        try:
            return (
                importlib.util.find_spec("pytesseract") is not None
                and shutil.which("tesseract") is not None
            )
        except Exception:
            return False

    def ocr_image(self, image_path) -> list[str]:
        import pytesseract

        text = pytesseract.image_to_string(load_image(image_path), config="--psm 6")
        return [line for line in text.splitlines() if line.strip()]
