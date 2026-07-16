"""Built-in OCR provider registrations."""

from .easyocr import EasyOcrProvider
from .paddle import PaddleProvider
from .tesseract import TesseractProvider

__all__ = ["EasyOcrProvider", "PaddleProvider", "TesseractProvider"]
