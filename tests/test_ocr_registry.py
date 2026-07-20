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


def _fake_tsv(words, confs, *, block=1, par=1, line=1):
    """Build an ``image_to_data(output_type=Output.DICT)``-shaped dict for one
    printed line: ``words``/``confs`` are parallel per-word text/confidence
    (Tesseract's 0-100 scale, ``-1`` for "no score")."""
    n = len(words)
    return {
        "text": list(words),
        "conf": list(confs),
        "block_num": [block] * n,
        "par_num": [par] * n,
        "line_num": [line] * n,
        "word_num": list(range(1, n + 1)),
    }


def test_tesseract_glyphive_uses_exact_constrained_config(monkeypatch):
    calls = []

    class _Output:
        DICT = "dict"

    def _image_to_data(image, *, config, output_type):
        calls.append((image, config, output_type))
        return _fake_tsv(["HAAAAA", "ABCD", "#ABCD"], [95, 40, 80])

    fake = SimpleNamespace(image_to_data=_image_to_data, Output=_Output)
    monkeypatch.setitem(sys.modules, "pytesseract", fake)
    monkeypatch.setattr(
        "glyphive.restore.ocr.providers.tesseract.load_image",
        lambda path: ("loaded", path),
    )

    lines = ocr.get("tesseract-glyphive").ocr_image("page.png")

    assert [line.text for line in lines] == ["HAAAAA ABCD #ABCD"]
    assert calls == [
        (
            ("loaded", "page.png"),
            "--psm 6 -c tessedit_char_whitelist=ABCDHKLMPRTVXY34# "
            "-c load_system_dawg=0 -c load_freq_dawg=0",
            "dict",
        )
    ]
    # Each word's confidence is broadcast across its own characters; the
    # printed space between words is confidence 1.0.
    conf = lines[0].char_conf
    assert conf[:6] == [0.95] * 6  # "HAAAAA"
    assert conf[6] == 1.0  # space
    assert conf[7:11] == [0.40] * 4  # "ABCD"
    assert conf[11] == 1.0  # space
    assert conf[12:] == [0.80] * 5  # "#ABCD"


def test_tesseract_unscored_word_yields_none_confidence(monkeypatch):
    def _image_to_data(image, *, config, output_type):
        return _fake_tsv(["AB"], [-1])

    class _Output:
        DICT = "dict"

    monkeypatch.setitem(
        sys.modules, "pytesseract", SimpleNamespace(image_to_data=_image_to_data, Output=_Output)
    )
    monkeypatch.setattr(
        "glyphive.restore.ocr.providers.tesseract.load_image", lambda path: path
    )

    lines = ocr.get("tesseract").ocr_image("page.png")
    assert lines[0].text == "AB"
    assert lines[0].char_conf == [None, None]


def test_tesseract_groups_multiple_lines_separately(monkeypatch):
    def _image_to_data(image, *, config, output_type):
        row0 = _fake_tsv(["first"], [90], line=1)
        row1 = _fake_tsv(["second"], [90], line=2)
        merged = {key: row0[key] + row1[key] for key in row0}
        return merged

    class _Output:
        DICT = "dict"

    monkeypatch.setitem(
        sys.modules, "pytesseract", SimpleNamespace(image_to_data=_image_to_data, Output=_Output)
    )
    monkeypatch.setattr(
        "glyphive.restore.ocr.providers.tesseract.load_image", lambda path: path
    )

    lines = ocr.get("tesseract").ocr_image("page.png")
    assert [line.text for line in lines] == ["first", "second"]


def test_easyocr_broadcasts_segment_confidence_per_character(monkeypatch):
    fake_reader = SimpleNamespace(
        readtext=lambda arr, *, detail, paragraph: [
            ((0, 0, 0, 0), "ABCD", 0.87),
            ((0, 0, 0, 0), "", 0.5),  # empty text is dropped
        ]
    )
    fake_easyocr = SimpleNamespace(Reader=lambda langs, gpu: fake_reader)
    monkeypatch.setitem(sys.modules, "easyocr", fake_easyocr)
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(array=lambda x: x))
    monkeypatch.setattr(
        "glyphive.restore.ocr.providers.easyocr.load_image", lambda path: path
    )

    lines = ocr.get("easyocr").ocr_image("page.png")

    assert len(lines) == 1
    assert lines[0].text == "ABCD"
    assert lines[0].char_conf == [0.87, 0.87, 0.87, 0.87]


def test_paddle_broadcasts_rec_scores_per_character(monkeypatch):
    fake_ocr = SimpleNamespace(
        predict=lambda arr: [
            {"rec_texts": ["ABCD", "EFGH"], "rec_scores": [0.9, 0.2]},
        ]
    )
    fake_paddleocr = SimpleNamespace(PaddleOCR=lambda **kwargs: fake_ocr)
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(array=lambda x: x))
    monkeypatch.setattr(
        "glyphive.restore.ocr.providers.paddle.load_image", lambda path: path
    )

    lines = ocr.get("paddle").ocr_image("page.png")

    assert [line.text for line in lines] == ["ABCD", "EFGH"]
    assert lines[0].char_conf == [0.9, 0.9, 0.9, 0.9]
    assert lines[1].char_conf == [0.2, 0.2, 0.2, 0.2]


def test_paddle_missing_score_yields_none_confidence(monkeypatch):
    fake_ocr = SimpleNamespace(
        predict=lambda arr: [{"rec_texts": ["AB"], "rec_scores": []}]
    )
    fake_paddleocr = SimpleNamespace(PaddleOCR=lambda **kwargs: fake_ocr)
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(array=lambda x: x))
    monkeypatch.setattr(
        "glyphive.restore.ocr.providers.paddle.load_image", lambda path: path
    )

    lines = ocr.get("paddle").ocr_image("page.png")
    assert lines[0].text == "AB"
    assert lines[0].char_conf is None
