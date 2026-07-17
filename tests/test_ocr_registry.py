"""Registry and lazy-import tests for OCR orchestration."""

import sys
from types import SimpleNamespace

import pytest

from glyphive.restore import ocr


def test_ocr_registry_names_and_import_laziness():
    assert ocr.names() == [
        "easyocr", "paddle", "tesseract", "tesseract-glyphive"
    ]
    assert "paddleocr" not in sys.modules
    assert "easyocr" not in sys.modules


def test_ocr_unknown_engine_is_actionable():
    with pytest.raises(ValueError, match=r"unknown OCR engine 'missing'.*paddle"):
        ocr.get("missing")


def test_ocr_duplicate_provider_names_are_rejected():
    existing = dict(ocr.OcrProvider._registry)
    try:
        with pytest.raises(ValueError, match="duplicate OCR provider name 'paddle'"):
            type(
                "DuplicateProvider",
                (ocr.OcrProvider,),
                {"name": "paddle", "ocr_image": lambda self, image_path: []},
            )
    finally:
        ocr.OcrProvider._registry.clear()
        ocr.OcrProvider._registry.update(existing)


def test_ocr_preference_order_can_use_fake_providers(monkeypatch):
    monkeypatch.setattr(ocr.OcrProvider._registry["paddle"], "is_available", classmethod(lambda cls: False))
    monkeypatch.setattr(ocr.OcrProvider._registry["easyocr"], "is_available", classmethod(lambda cls: True))
    monkeypatch.setattr(ocr.OcrProvider._registry["tesseract"], "is_available", classmethod(lambda cls: True))
    monkeypatch.setattr(ocr.OcrProvider._registry["tesseract-glyphive"], "is_available", classmethod(lambda cls: False))
    assert ocr.available_engines() == ["easyocr", "tesseract"]


def test_tesseract_glyphive_uses_exact_constrained_config(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        image_to_string=lambda image, *, config: calls.append((image, config))
        or "  \nHAAAAA ABCD #ABCD\n"
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake)
    monkeypatch.setattr(
        "glyphive.restore.ocr.providers.tesseract.load_image",
        lambda path: ("loaded", path),
    )

    lines = ocr.get("tesseract-glyphive").ocr_image("page.png")

    assert lines == ["HAAAAA ABCD #ABCD"]
    assert calls == [
        (
            ("loaded", "page.png"),
            "--psm 6 -c tessedit_char_whitelist=ABCDHKLMPRTVXY34# "
            "-c load_system_dawg=0 -c load_freq_dawg=0",
        )
    ]
