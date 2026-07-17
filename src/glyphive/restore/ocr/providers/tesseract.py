"""Tesseract provider with lazy third-party imports."""

from __future__ import annotations

import importlib.util
import shutil

from .._base import OcrProvider
from ._image import load_image

_GLYPHIVE_CONFIG = (
    "--psm 6 "
    "-c tessedit_char_whitelist=ABCDHKLMPRTVXY34# "
    "-c load_system_dawg=0 "
    "-c load_freq_dawg=0"
)


def _is_available() -> bool:
    try:
        return (
            importlib.util.find_spec("pytesseract") is not None
            and shutil.which("tesseract") is not None
        )
    except Exception:
        return False


class TesseractProvider(OcrProvider):
    name = "tesseract"

    @classmethod
    def is_available(cls) -> bool:
        return _is_available()

    def ocr_image(self, image_path) -> list[str]:
        import pytesseract

        text = pytesseract.image_to_string(load_image(image_path), config="--psm 6")
        return [line for line in text.splitlines() if line.strip()]


class TesseractGlyphiveProvider(OcrProvider):
    """Tesseract constrained to Glyphive's measured machine alphabet."""

    name = "tesseract-glyphive"

    @classmethod
    def is_available(cls) -> bool:
        return _is_available()

    def ocr_image(self, image_path) -> list[str]:
        import pytesseract

        text = pytesseract.image_to_string(
            load_image(image_path), config=_GLYPHIVE_CONFIG
        )
        return [line for line in text.splitlines() if line.strip()]
