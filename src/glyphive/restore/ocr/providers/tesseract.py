"""Tesseract provider with lazy third-party imports."""

from __future__ import annotations

import importlib.util
import shutil
import typing as _ty

from .._base import OcrLine, OcrProvider
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


def _lines_from_tsv(data: _ty.Mapping[str, _ty.Sequence]) -> _ty.List[OcrLine]:
    """Reconstruct OCR lines (+ per-character confidence) from ``image_to_data``.

    ``image_to_data`` (TSV levels 1..5 = page/block/paragraph/line/word) is
    the finest granularity Tesseract's stable per-token API offers -- there
    is no true per-SYMBOL confidence available this way (level 5 is the WORD
    level), so each word's confidence (Tesseract: 0-100, ``-1`` for "no
    score") is broadcast across every character of that word; the single
    space PRINTED between words is given confidence ``1.0`` (spaces are not
    subject to glyph-misread the way payload characters are, and Tesseract
    reports no confidence for the gap between words anyway). Rows are
    grouped by ``(block_num, par_num, line_num)`` and joined in
    ``word_num`` order to reconstruct each printed line, matching
    Tesseract's own reading order. A word Tesseract could not score at all
    (``conf == -1``) contributes ``None`` per character rather than a
    fabricated value -- callers must keep treating ``None`` as "no evidence
    this character is bad", not as low confidence.
    """
    texts = data.get("text", [])
    n = len(texts)
    groups: "_ty.Dict[_ty.Tuple[int, int, int], _ty.List[int]]" = {}
    order: "_ty.List[_ty.Tuple[int, int, int]]" = []
    for i in range(n):
        text = texts[i]
        if not text or not text.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(i)

    lines: _ty.List[OcrLine] = []
    for key in order:
        indices = sorted(groups[key], key=lambda i: data["word_num"][i])
        text_parts: _ty.List[str] = []
        conf_parts: _ty.List[_ty.Optional[float]] = []
        for pos, i in enumerate(indices):
            word = texts[i]
            try:
                raw_conf = float(data["conf"][i])
            except (TypeError, ValueError):
                raw_conf = -1.0
            word_conf = None if raw_conf < 0 else raw_conf / 100.0
            if pos:
                text_parts.append(" ")
                conf_parts.append(1.0)
            text_parts.append(word)
            conf_parts.extend([word_conf] * len(word))
        line_text = "".join(text_parts)
        if line_text.strip():
            lines.append(OcrLine(line_text, conf_parts))
    return lines


class TesseractProvider(OcrProvider):
    name = "tesseract"

    @classmethod
    def is_available(cls) -> bool:
        return _is_available()

    def ocr_image(self, image_path) -> _ty.List[OcrLine]:
        import pytesseract

        data = pytesseract.image_to_data(
            load_image(image_path), config="--psm 6", output_type=pytesseract.Output.DICT
        )
        return _lines_from_tsv(data)


class TesseractGlyphiveProvider(OcrProvider):
    """Tesseract constrained to Glyphive's measured machine alphabet."""

    name = "tesseract-glyphive"

    @classmethod
    def is_available(cls) -> bool:
        return _is_available()

    def ocr_image(self, image_path) -> _ty.List[OcrLine]:
        import pytesseract

        data = pytesseract.image_to_data(
            load_image(image_path), config=_GLYPHIVE_CONFIG, output_type=pytesseract.Output.DICT
        )
        return _lines_from_tsv(data)
