from __future__ import annotations

import pytest


def test_auto_input_renders_pdf_then_ocr(tmp_path, monkeypatch):
    from glyphive.cli import _common
    from glyphive.restore import ocr

    source = tmp_path / "scan.pdf"
    source.write_bytes(b"pdf")
    seen = {}

    def fake_render(path, destination):
        seen["render"] = path.name
        return [destination / "scan-0001.png", destination / "scan-0002.png"]

    def fake_ocr(paths, *, engine=None):
        seen.setdefault("ocr", []).append(([path.name for path in paths], engine))
        return [[f"line-{paths[0].name}"]]

    monkeypatch.setattr(
        "glyphive.restore.document_images.render_document_images", fake_render
    )
    monkeypatch.setattr(ocr, "ocr_pages", fake_ocr)

    assert _common.load_input_lines(source, engine="mock") == [
        "line-scan-0001.png"
    ]
    assert seen == {
        "render": "scan.pdf",
        "ocr": [(["scan-0001.png", "scan-0002.png"], "mock")],
    }


def test_auto_directory_accepts_mixed_supported_inputs(tmp_path, monkeypatch):
    from glyphive.cli import _common
    from glyphive.restore import ocr

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "a.txt").write_text("transcript\n", encoding="utf-8")
    (inputs / "b.png").write_bytes(b"png")
    monkeypatch.setattr(
        ocr,
        "ocr_pages",
        lambda paths, *, engine=None: [[f"ocr-{paths[0].name}"]],
    )
    assert _common.load_input_lines(inputs) == ["transcript", "ocr-b.png"]


def test_image_magic_wins_over_missing_or_wrong_extension(tmp_path, monkeypatch):
    from glyphive.cli import _common
    from glyphive.restore import ocr

    image = tmp_path / "renamed.txt"
    image.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    monkeypatch.setattr(
        ocr, "ocr_pages", lambda paths, *, engine=None: [["ocr-page"]]
    )
    assert _common.load_input_lines(image) == ["ocr-page"]


def test_pdf_magic_wins_over_extension(tmp_path, monkeypatch):
    from glyphive.cli import _common
    from glyphive.restore import ocr

    pdf = tmp_path / "scan.bin"
    pdf.write_bytes(b"%PDF-1.7\n")
    monkeypatch.setattr(
        "glyphive.restore.document_images.render_document_images",
        lambda path, destination: [destination / "page.png"],
    )
    monkeypatch.setattr(ocr, "ocr_pages", lambda paths, *, engine=None: [["pdf-page"]])
    assert _common.load_input_lines(pdf) == ["pdf-page"]


def test_unknown_binary_input_fails_clearly(tmp_path):
    from glyphive.cli._common import load_input_lines

    source = tmp_path / "unknown.bin"
    source.write_bytes(b"\x00\xff\x00\xff")
    with pytest.raises(ValueError, match="cannot detect supported input type"):
        load_input_lines(source)


def test_list_uses_auto_input_and_forwards_ocr_engine(tmp_path, monkeypatch):
    from glyphive import cli
    from glyphive.cli import list as list_command
    from glyphive.restore import decode

    seen = {}
    monkeypatch.setattr(
        list_command,
        "load_input_lines",
        lambda source, engine=None: seen.update(source=source, engine=engine) or [],
    )
    monkeypatch.setattr(
        decode,
        "decode_document",
        lambda lines: (
            {
                "v": 1,
                "codec": "g1",
                "comp": "none",
                "files": 0,
                "bytes": 0,
                "pages": 1,
            },
            b"",
        ),
    )
    monkeypatch.setattr(list_command._archive, "iter_records", lambda raw: [])

    assert cli.run(
        ["list", "-f", str(tmp_path / "scan.pdf"), "--ocr-engine", "mock"]
    ) == 0
    assert seen["source"].name == "scan.pdf"
    assert seen["engine"] == "mock"


def test_docx_requires_libreoffice(tmp_path, monkeypatch):
    from glyphive.restore import document_images

    source = tmp_path / "scan.docx"
    source.write_bytes(b"docx")
    monkeypatch.setattr(document_images.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="requires LibreOffice"):
        document_images.render_document_images(source, tmp_path / "pages")


@pytest.mark.parametrize("dpi,blur", [(0, 0), (300, -1)])
def test_document_render_options_are_validated(tmp_path, dpi, blur):
    from glyphive.restore.document_images import render_document_images

    with pytest.raises(ValueError):
        render_document_images(
            tmp_path / "scan.pdf", tmp_path / "pages", dpi=dpi, blur=blur
        )
