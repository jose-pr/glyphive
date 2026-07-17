"""Built-in OCR provider registrations."""

from .easyocr import EasyOcrProvider
from .paddle import PaddleProvider
from .tesseract import TesseractGlyphiveProvider, TesseractProvider

__all__ = [
    "EasyOcrProvider",
    "PaddleProvider",
    "TesseractGlyphiveProvider",
    "TesseractProvider",
]
