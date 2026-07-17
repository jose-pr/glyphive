"""Lazy OCR provider registry and voting orchestration."""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Optional

from ._base import OcrProvider
from .providers import (
    EasyOcrProvider,
    PaddleProvider,
    TesseractGlyphiveProvider,
    TesseractProvider,
)

_ENGINE_PREFERENCE = ("paddle", "easyocr", "tesseract")
_INSTALL_HINT = (
    "no OCR engine available; install one, e.g. "
    "pip install glyphive[ocr] and paddleocr"
)

__all__ = [
    "OcrProvider",
    "available",
    "available_engines",
    "get",
    "names",
    "ocr_image",
    "ocr_pages",
    "ocr_vote",
]


def get(name: str) -> OcrProvider:
    return OcrProvider.get(name)


def names() -> list[str]:
    return OcrProvider.names()


def available() -> list[str]:
    """Return registered OCR provider names currently available to use."""
    return OcrProvider.available()


def available_engines() -> list[str]:
    """Return available providers in the documented preference order."""
    result = []
    ordered_names = list(_ENGINE_PREFERENCE)
    ordered_names.extend(name for name in names() if name not in ordered_names)
    for name in ordered_names:
        try:
            if get(name).is_available():
                result.append(name)
        except Exception:
            continue
    return result


def _select_engine(engine: Optional[str]) -> str:
    available = available_engines()
    if engine is None:
        if not available:
            raise RuntimeError(_INSTALL_HINT)
        return available[0]
    registered = names()
    if engine not in registered:
        raise ValueError(
            f"unknown OCR engine {engine!r}; registered OCR engines: "
            f"{', '.join(registered) or '(none)'}"
        )
    if engine not in available:
        raise RuntimeError(
            f"OCR engine {engine!r} is not available; available engines: "
            f"{available or 'none'} ({_INSTALL_HINT})"
        )
    return engine


def ocr_image(image_path, *, engine: Optional[str] = None) -> list[str]:
    """OCR one image with a selected or highest-preference provider."""
    return get(_select_engine(engine)).ocr_image(image_path)


def ocr_pages(
    image_paths: Iterable, *, engine: Optional[str] = None
) -> list[list[str]]:
    """OCR several images, resolving one provider before any page work."""
    provider = get(_select_engine(engine))
    return [provider.ocr_image(path) for path in image_paths]


def ocr_vote(image_path, *, engines: list[str]) -> list[str]:
    """Return a majority-vote hint; CRC/RS remains the correctness oracle."""
    if not engines:
        raise ValueError("ocr_vote requires at least one engine")
    providers = [get(_select_engine(name)) for name in engines]
    per_engine = [provider.ocr_image(image_path) for provider in providers]
    base = per_engine[0]
    voted: list[str] = []
    for index, base_line in enumerate(base):
        votes: Counter[str] = Counter(
            lines[index] for lines in per_engine if index < len(lines)
        )
        if not votes:
            voted.append(base_line)
            continue
        top = max(votes.values())
        for lines in per_engine:
            if index < len(lines) and votes[lines[index]] == top:
                voted.append(lines[index])
                break
    return voted
