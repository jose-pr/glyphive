"""Tests for the renderers (text / pdf / docx).

All optional backends (fpdf2, python-docx) are installed via ``[all]`` on this
machine, so NONE of these tests skip here. The text renderer's round-trip through
``layout.read_pages`` + ``codec.get("base16g-crc16-rs").decode`` is the key correctness check.
"""

import hashlib
from importlib import resources
import os
import random

import pytest

from glyphive import codec, layout


base16g_codec = codec.get("base16g-crc16-rs")
from glyphive import render as render_mod
from glyphive.render import lines_per_page_for, render
from glyphive.render.formats.text import FORM_FEED
from glyphive.render.formats.pdf import _fitted_font_size, _line_character_spacing
from glyphive.render.formats.pdf import PdfRenderFormat


def _make_pages(nbytes=800, seed=5):
    rng = random.Random(seed)
    data = bytes(rng.randrange(256) for _ in range(nbytes))
    encoded = base16g_codec.encode(data)
    meta = {
        "codec": "base16g-crc16-rs",
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
    assert base16g_codec.decode(encoded_lines) == data
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


def test_pdf_overflowing_frame_line_fails_loud_but_header_may_shrink(tmp_path):
    """A frame that would overflow the width fails; the human header may shrink.

    Real-world scan finding (2026-07-17): silently shrinking a machine/data
    frame (H/L/P/T) to fit distorts glyphs (hurting OCR) and hides a
    misconfigured font/size/margin/width -- so the PDF renderer now fails loud
    on an overflowing frame. The display-only ``#!glyphive`` header is exempt
    (restore never trusts it) and is still scaled to fit.
    """
    from glyphive.layout import Page

    renderer = PdfRenderFormat()

    # A full-width L frame at a very large size cannot fit US-Letter width.
    long_frame = "L" + "A" * 90 + " " + "B" * 60 + " #ABCD"
    frame_page = [Page(number=1, total=1, text_lines=[long_frame], encoded_lines=[long_frame])]
    with pytest.raises(ValueError, match="protected frame line overflows"):
        renderer.render(frame_page, tmp_path / "frame.pdf", font_size=40)

    # The same-length line as a human ``#!glyphive`` header must NOT raise --
    # it is display-only and allowed to shrink to fit.
    header = "#!glyphive " + "x" * 200
    header_page = [Page(number=1, total=1, text_lines=[header], encoded_lines=[])]
    renderer.render(header_page, tmp_path / "header.pdf", font_size=40)
    assert (tmp_path / "header.pdf").exists()


def test_pdf_parity_document_renders_and_q_frame_overflow_fails_loud(tmp_path):
    """A K>0 PDF renders (Q rows now within width), and an over-wide Q raises (F2).

    F2 regression: ``_FRAME_KINDS`` omitted ``Q``, so an overflowing parity
    frame silently shrank instead of failing loud, and Q was excluded from the
    fixed-width glyph max.
    """
    from glyphive.layout import Page

    data, _pages = _make_pages(nbytes=1600, seed=9)
    meta = {
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "files": 1,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    encoded = base16g_codec.encode(data)
    parity_pages = layout.paginate(
        encoded, meta, lines_per_page=lines_per_page_for(11.0), parity_pages=2
    )
    # After F1, Q rows fit within the safe width, so a normal-size render works.
    render(parity_pages, tmp_path / "parity.pdf", "pdf")
    assert (tmp_path / "parity.pdf").exists()

    # A deliberately over-wide Q frame must fail loud, not silently shrink.
    renderer = PdfRenderFormat()
    wide_q = "Q" + "A" * 90 + " " + "B" * 60 + " #ABCD"
    q_page = [Page(number=1, total=1, text_lines=[wide_q], encoded_lines=[wide_q])]
    with pytest.raises(ValueError, match="protected frame line overflows"):
        renderer.render(q_page, tmp_path / "wide_q.pdf", font_size=40)


def test_pdf_geometric_capacity_tracks_font_size_margins_and_spacing():
    """The underlying (uncapped) glyph-width measurement scales as expected."""
    renderer = PdfRenderFormat()
    base = renderer._geometric_payload_capacity(font="courier", font_size=8, page_margin_pt=36)
    compact = renderer._geometric_payload_capacity(font="courier", font_size=8, page_margin_pt=12)
    larger = renderer._geometric_payload_capacity(font="courier", font_size=11, page_margin_pt=36)
    tracked = renderer._geometric_payload_capacity(
        font="courier", font_size=8, page_margin_pt=36, character_spacing_pt=0.2
    )

    assert base >= 60 and base % 2 == 0
    assert compact > base
    assert larger < base
    assert tracked < base


def test_geometric_payload_capacity_public_hook():
    """The public geometric hook: uncapped on PDF, None on formats w/o metrics."""
    from glyphive.render.formats.text import TextRenderFormat

    pdf = PdfRenderFormat()
    geo = pdf.geometric_payload_capacity(font="ocr-b", font_size=6)
    capped = pdf.payload_capacity(font="ocr-b", font_size=6)
    assert geo is not None and geo >= capped and geo > 60
    assert TextRenderFormat().geometric_payload_capacity(font_size=8) is None


def test_pdf_payload_capacity_is_clamped_to_the_measured_safe_width():
    """The public API never exceeds the one width this project has OCR evidence for.

    Real-content testing (2026-07-17 gallery run) found OCR-B 6pt's ~90-char
    geometric fit measurably less reliable than the 60-char width every
    published OCR-safety measurement (including OCR-B's own "dense" preset)
    was actually taken at -- payload_capacity must not silently widen past 60.
    """
    renderer = PdfRenderFormat()
    uncapped = renderer._geometric_payload_capacity(font="ocr-b", font_size=6, page_margin_pt=36)
    capped = renderer.payload_capacity(font="ocr-b", font_size=6, page_margin_pt=36)

    assert uncapped > 60  # the geometry really would allow more
    assert capped == 60


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


def test_bundled_dejavu_sans_mono_is_pinned_and_renders_pdf(tmp_path):
    font = resources.files("glyphive.assets.fonts.dejavu_sans_mono").joinpath(
        "DejaVuSansMono.ttf"
    )
    assert hashlib.sha256(font.read_bytes()).hexdigest() == (
        "b4a6c3e4faab8773f4ff761d56451646409f29abedd68f05d38c2df667d3c582"
    )
    _data, pages = _make_pages(nbytes=80)
    output = tmp_path / "dejavu.pdf"
    render(pages, output, "pdf", font="dejavu-sans-mono", font_size=8)
    assert output.read_bytes().startswith(b"%PDF")


def test_unsupported_pdf_font_error_lists_bundled_fonts():
    from glyphive.render.formats.pdf import PdfRenderFormat
    from glyphive.layout import Page

    renderer = PdfRenderFormat()
    page = [Page(number=1, total=1, text_lines=["#!glyphive"], encoded_lines=[])]
    with pytest.raises(ValueError, match="dejavu-sans-mono") as excinfo:
        renderer.render(page, "/tmp/x.pdf", font="no-such-font")
    assert "ocr-b" in str(excinfo.value)


def test_pdf_accepts_explicit_font_file(tmp_path):
    font = resources.files("glyphive.assets.fonts.ocr_b").joinpath("OCR-B.ttf")
    _data, pages = _make_pages(nbytes=80)
    output = tmp_path / "custom-font.pdf"
    render(pages, output, "pdf", font=os.fspath(font), font_size=8)
    assert output.read_bytes().startswith(b"%PDF")


def test_pdf_resolves_font_name_from_system_store(tmp_path, monkeypatch):
    """A bare family name is resolved against the OS font stores by file stem."""
    from glyphive.render.formats import pdf as pdf_mod
    from pathlib_next import Path

    # Fake system font dir holding a file named after the requested family.
    fake_store = tmp_path / "fake-fonts"
    fake_store.mkdir()
    src = resources.files("glyphive.assets.fonts.ocr_b").joinpath("OCR-B.ttf")
    (fake_store / "Fake Mono.ttf").write_bytes(src.read_bytes())
    monkeypatch.setattr(pdf_mod, "_system_font_dirs", lambda: [Path(str(fake_store))])

    _data, pages = _make_pages(nbytes=80)
    output = tmp_path / "by-name.pdf"
    # Not core, not bundled, not a path -> must be found in the fake store.
    render(pages, output, "pdf", font="Fake Mono", font_size=8)
    assert output.read_bytes().startswith(b"%PDF")


def test_pdf_resolves_font_name_from_true_family_name(tmp_path, monkeypatch):
    """A name matching the font's TRUE family (not its filename) still resolves.

    Regression for the Windows case where a font's canonical file isn't named
    after its family (e.g. Consolas ships as ``consola.ttf``): the filename-
    stem pass must fall through to reading the font's own ``name`` table
    rather than reporting "not found" just because the file happens to be
    named something else. Uses a real TTF (the bundled OCR-B) with its
    internal family name rewritten to something that is neither its filename
    nor any core/bundled font name, so the test can only pass via the true
    name-table lookup -- not the filename-stem pass or the core/bundled
    dispatch that come first in ``registered_pdf_font``.
    """
    from fontTools.ttLib import TTFont

    from glyphive.render.formats import pdf as pdf_mod
    from pathlib_next import Path

    fake_store = tmp_path / "fake-fonts"
    fake_store.mkdir()
    src = resources.files("glyphive.assets.fonts.ocr_b").joinpath("OCR-B.ttf")
    renamed = fake_store / "totally-unrelated-filename.ttf"
    with resources.as_file(src) as font_path:
        font = TTFont(str(font_path))
    name_table = font["name"]
    for record in name_table.names:
        if record.nameID in (1, 4, 6, 16):
            record.string = "Totally Custom Family Name"
    font.save(str(renamed))
    monkeypatch.setattr(pdf_mod, "_system_font_dirs", lambda: [Path(str(fake_store))])

    _data, pages = _make_pages(nbytes=80)
    output = tmp_path / "by-true-name.pdf"
    render(pages, output, "pdf", font="Totally Custom Family Name", font_size=8)
    assert output.read_bytes().startswith(b"%PDF")


def test_pdf_unknown_font_reports_system_store_search(tmp_path, monkeypatch):
    """When the name is nowhere (incl. an empty system store), the error says so."""
    from glyphive.render.formats import pdf as pdf_mod
    from glyphive.render.formats.pdf import PdfRenderFormat
    from glyphive.layout import Page

    monkeypatch.setattr(pdf_mod, "_system_font_dirs", lambda: [])  # no stores
    renderer = PdfRenderFormat()
    page = [Page(number=1, total=1, text_lines=["#!glyphive"], encoded_lines=[])]
    with pytest.raises(ValueError, match="OS font stores") as excinfo:
        renderer.render(page, str(tmp_path / "x.pdf"), font="no-such-font")
    assert "dejavu-sans-mono" in str(excinfo.value)


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
# with codec.engine._parse_line's tolerance for OCR-inserted interior spaces.
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
    # nsym_line=0: isolates this from the (separately tested) line-parity
    # field so the encoded line has an unambiguous bare 3-token shape --
    # _looks_like_encoded's own line_parity_chars default is 0.
    data = bytes(range(40))
    lines = base16g_codec.encode(data, nsym_line=0)
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
