"""The ``tesseract-glyphive-ocrb`` OCR provider (fine-tuned model).

Mirrors the core ``tesseract-glyphive`` provider but points Tesseract at this
package's bundled ``.traineddata`` via ``--tessdata-dir`` and selects it with
``-l``. Reuses the same measured-safe base16c character whitelist and PSM.
"""

from __future__ import annotations

import importlib.util
import shutil
from importlib import resources

from glyphive.restore.ocr import OcrProvider

#: The traineddata file bundled in this package (added at publish time).
_MODEL_LANG = "glyphiveocrb"
_MODEL_FILE = f"{_MODEL_LANG}.traineddata"

#: Same channel constraints as the core tesseract-glyphive provider.
_WHITELIST = (
    "-c tessedit_char_whitelist=ABCDHKLMPRTVXY34# "
    "-c load_system_dawg=0 "
    "-c load_freq_dawg=0"
)


def _model_dir() -> "str | None":
    """Directory holding this package's traineddata, or None if absent.

    The ``.traineddata`` is a large binary added only when the model wheel is
    built for release; a source checkout may not carry it. Absent → the
    provider reports unavailable and glyphive falls back to a core engine.
    """
    try:
        resource = resources.files(__package__).joinpath(_MODEL_FILE)
    except (ModuleNotFoundError, AttributeError):
        return None
    try:
        if resource.is_file():
            # resources.as_file would give a temp path; tesseract needs a
            # stable dir, and for a real installed wheel the file is on disk.
            import os

            return os.path.dirname(str(resource))
    except (OSError, ValueError):
        return None
    return None


class OcrbGlyphiveProvider(OcrProvider):
    """Tesseract constrained to the base16c alphabet, using the ocrb model."""

    name = "tesseract-glyphive-ocrb"

    @classmethod
    def is_available(cls) -> bool:
        try:
            return (
                importlib.util.find_spec("pytesseract") is not None
                and shutil.which("tesseract") is not None
                and _model_dir() is not None
            )
        except Exception:
            return False

    def ocr_image(self, image_path) -> "list[str]":
        import pytesseract

        from glyphive.restore.ocr.providers._image import load_image

        model_dir = _model_dir()
        config = f'--tessdata-dir "{model_dir}" -l {_MODEL_LANG} --psm 6 {_WHITELIST}'
        text = pytesseract.image_to_string(load_image(image_path), config=config)
        return [line for line in text.splitlines() if line.strip()]
