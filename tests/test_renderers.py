"""Tests for the renderers (text / pdf / docx).

All optional backends (fpdf2, python-docx) are installed via ``[all]`` on this
machine, so NONE of these tests skip here. The text renderer's round-trip through
``layout.read_pages`` + ``codec.get("g1").decode`` is the key correctness check.
"""

import hashlib
from importlib import resources
import os
import random

import pytest

from glyphive import codec, layout


g1 = codec.get("g1")
from glyphive import render as render_mod
from glyphive.render import lines_per_page_for, render
from glyphive.render.formats.text import FORM_FEED
from glyphive.render.formats.pdf import _fitted_font_size, _line_character_spacing
from glyphive.render.formats.pdf import PdfRenderFormat


def _make_pages(nbytes=800, seed=5):
    rng = random.Random(seed)
    data = bytes(rng.randrange(256) for _ in range(nbytes))
    encoded = g1.encode(data)
    meta = {
        "codec": "g1",
        "comp": "none",
        "files": 1,
        "bytes": nbytes,
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    lpp = lines_per_page_for(11.0)
    pages = layout.paginate(encoded, meta, lines_per_page=lpp)
    return data, pages


@pytest.mark.parametrize(
    "fmt,ext,magic",
    [
        ("text", "txt", None),
        ("pdf", "pdf", b"%PDF"),
        ("docx", "docx", b"PK"),
    ],
)
def test_render_writes_nonempty_file(tmp_path, fmt, ext, magic):
    _data, pages = _make_pages()
    out = tmp_path / f"out.{ext}"
    render(pages, out, fmt)
    assert out.exists()
    raw = out.read_bytes()
    assert len(raw) > 0
    if magic is not None:
        assert raw.startswith(magic)


def test_text_render_roundtrips(tmp_path):
    """The text renderer must be byte-preserving: read it back, strip the form
    feeds, and it decodes to the exact original bytes."""
    data, pages = _make_pages(nbytes=1500, seed=17)
    out = tmp_path / "doc.txt"
    render(pages, out, "text")

    text = out.read_text(encoding="utf-8")
    # Pages are separated by form feeds; split them out and re-join as lines.
    text_lines = text.replace(FORM_FEED, "\n").splitlines()

    meta, encoded_lines = layout.read_pages(text_lines)
    assert g1.decode(encoded_lines) == data
    # sha of decoded matches the header's recorded digest.
    assert hashlib.sha256(data).hexdigest() == meta["sha256"]


def test_render_rejects_unknown_format(tmp_path):
    _data, pages = _make_pages()
    with pytest.raises(ValueError):
        render(pages, tmp_path / "x.out", "bogus")


def test_page_rows_scale_with_font_size_and_margins():
    assert lines_per_page_for(8.0) > lines_per_page_for(11.0)
    assert lines_per_page_for(8.0, page_margin_pt=12.0) > lines_per_page_for(8.0)


def test_pdf_long_lines_fit_available_width_without_wrapping():
    assert _fitted_font_size(8.0, 400.0, 500.0) == 8.0
    assert _fitted_font_size(8.0, 1000.0, 500.0) == 4.0
    assert _fitted_font_size(
        8.0,
        400.0,
        500.0,
        character_spacing_pt=2.0,
        character_count=101,
    ) == 6.0


def test_pdf_payload_capacity_tracks_font_size_margins_and_spacing():
    renderer = PdfRenderFormat()
    base = renderer.payload_capacity(font="courier", font_size=8, page_margin_pt=36)
    compact = renderer.payload_capacity(font="courier", font_size=8, page_margin_pt=12)
    larger = renderer.payload_capacity(font="courier", font_size=11, page_margin_pt=36)
    tracked = renderer.payload_capacity(
        font="courier", font_size=8, page_margin_pt=36, character_spacing_pt=0.2
    )

    assert base is not None and base >= 60 and base % 2 == 0
    assert compact is not None and compact > base
    assert larger is not None and larger < base
    assert tracked is not None and tracked < base


def test_pdf_justify_distributes_tracking_across_available_width():
    assert _line_character_spacing(
        "ABCDE",
        alignment="justify",
        base_spacing_pt=0.25,
        text_width=40.0,
        available_width=48.0,
    ) == 2.0
    assert _line_character_spacing(
        "ABCDE",
        alignment="center",
        base_spacing_pt=0.25,
        text_width=40.0,
        available_width=48.0,
    ) == 0.25


@pytest.mark.parametrize("alignment", ["left", "center", "justify"])
def test_pdf_renders_alignment_and_tracking(tmp_path, alignment):
    _data, pages = _make_pages(nbytes=80)
    output = tmp_path / f"{alignment}.pdf"
    render(
        pages,
        output,
        "pdf",
        font_size=8,
        horizontal_alignment=alignment,
        character_spacing_pt=0.2,
    )
    assert output.read_bytes().startswith(b"%PDF")


def test_docx_writes_distributed_alignment_and_tracking(tmp_path):
    import docx

    _data, pages = _make_pages(nbytes=80)
    output = tmp_path / "distributed.docx"
    render(
        pages,
        output,
        "docx",
        horizontal_alignment="justify",
        character_spacing_pt=0.25,
    )
    document = docx.Document(output)
    paragraph = next(p for p in document.paragraphs if p.text)
    assert paragraph.alignment == 4
    xml = paragraph.runs[0]._element.xml
    assert 'w:spacing w:val="5"' in xml


def test_physical_renderer_rejects_invalid_layout_controls(tmp_path):
    _data, pages = _make_pages(nbytes=80)
    with pytest.raises(ValueError, match="horizontal_alignment"):
        render(pages, tmp_path / "bad.pdf", "pdf", horizontal_alignment="right")
    with pytest.raises(ValueError, match="character_spacing_pt"):
        render(pages, tmp_path / "bad.docx", "docx", character_spacing_pt=-0.1)


def test_bundled_ocr_b_font_is_pinned_and_renders_pdf(tmp_path):
    font = resources.files("glyphive.assets.fonts.ocr_b").joinpath("OCR-B.ttf")
    assert hashlib.sha256(font.read_bytes()).hexdigest() == (
        "367d876cca948ecd4900851f6e85687cbb6e71de9d0d2f36348edec5655526af"
    )
    _data, pages = _make_pages(nbytes=80)
    output = tmp_path / "ocr-b.pdf"
    render(pages, output, "pdf", font="ocr-b", font_size=8)
    assert output.read_bytes().startswith(b"%PDF")


def test_pdf_accepts_explicit_font_file(tmp_path):
    font = resources.files("glyphive.assets.fonts.ocr_b").joinpath("OCR-B.ttf")
    _data, pages = _make_pages(nbytes=80)
    output = tmp_path / "custom-font.pdf"
    render(pages, output, "pdf", font=os.fspath(font), font_size=8)
    assert output.read_bytes().startswith(b"%PDF")


def test_all_backends_present():
    # Guard-test: on this machine the optional backends ARE installed, so
    # importing the renderer submodules must succeed (0 skips expected here).
    # If this fails, the [all] extra is not installed and pdf/docx would skip.
    import importlib

    for name in (
        "glyphive.render.formats.text",
        "glyphive.render.formats.pdf",
        "glyphive.render.formats.docx",
    ):
        importlib.import_module(name)
    # fpdf and docx backends importable.
    import fpdf  # noqa: F401
    import docx  # noqa: F401


def test_render_registry_lists_formats_and_rejects_unknown():
    assert render_mod.names() == ["docx", "hybrid", "pdf", "qr", "text"]
    assert render_mod.get("text") is not render_mod.get("text")
    with pytest.raises(ValueError, match=r"unknown render format 'missing'.*docx"):
        render_mod.get("missing")


@pytest.mark.parametrize("fmt", ["qr", "hybrid"])
def test_qr_pdf_renderers_when_extra_is_installed(tmp_path, fmt):
    pytest.importorskip("segno")
    pytest.importorskip("zxingcpp")
    _data, pages = _make_pages(nbytes=80)
    output = tmp_path / f"{fmt}.pdf"

    render(pages, output, fmt, font_size=7)

    assert output.read_bytes().startswith(b"%PDF")


# --------------------------------------------------------------------------- #
# Structural frame parsing: layout._looks_like_encoded must agree
# with codec.g1._parse_line's tolerance for OCR-inserted interior spaces.
# --------------------------------------------------------------------------- #
def test_looks_like_encoded_tolerates_captured_ocr_transcript_line():
    # Re-pinned for the new 5-char index token (INDEX_WIDTH 4 -> 5 alongside
    # the alphabet's bit-width change).
    ocr_line = (
        "LMYCVH 8WRG2380000627WB10000000001FYWZQH4 "
        "6F1IWO0C6DJ64R320015D1J4QP90 #1RBN"
    )
    assert layout._looks_like_encoded(ocr_line) is True


def test_looks_like_encoded_tolerates_two_interior_spaces():
    data = bytes(range(40))
    lines = g1.encode(data)
    line = next(l for l in lines if l.startswith("L"))
    label, payload, check = line.split()
    noisy_payload = payload[:10] + " " + payload[10:20] + " " + payload[20:]
    noisy_line = f"{label} {noisy_payload} {check}"
    assert len(noisy_line.split()) == 5
    assert layout._looks_like_encoded(noisy_line) is True


def test_looks_like_encoded_rejects_page_footer():
    # Starts with "P" (like a parity-line kind) and has 3 tokens, but is not
    # a real frame -- must not be mistaken for one.
    footer = "PAGE 1/1 sha256=ea5b07a93a037a43"
    assert layout._looks_like_encoded(footer) is False
