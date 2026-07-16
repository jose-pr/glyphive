"""Tests for the renderers (text / pdf / docx).

All optional backends (fpdf2, python-docx) are installed via ``[all]`` on this
machine, so NONE of these tests skip here. The text renderer's round-trip through
``layout.read_pages`` + ``codec.get("g1").decode`` is the key correctness check.
"""

import hashlib
import os
import random

import pytest

from glyphive import codec, layout


g1 = codec.get("g1")
from glyphive import render as render_mod
from glyphive.render import lines_per_page_for, render
from glyphive.render.formats.text import FORM_FEED


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
    assert render_mod.names() == ["docx", "pdf", "text"]
    assert render_mod.get("text") is not render_mod.get("text")
    with pytest.raises(ValueError, match=r"unknown render format 'missing'.*docx"):
        render_mod.get("missing")
