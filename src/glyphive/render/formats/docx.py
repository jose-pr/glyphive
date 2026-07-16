"""Word renderer with a lazy python-docx import."""

from __future__ import annotations

import io as _io
import os as _os
import typing as _ty

from pathlib_next import Path

from glyphive.layout import Page
from glyphive.render._base import DEFAULT_DOCX_FONT, RenderFormat


class DocxRenderFormat(RenderFormat):
    name = "docx"

    @classmethod
    def is_available(cls) -> bool:
        try:
            import importlib.util

            return importlib.util.find_spec("docx") is not None
        except Exception:
            return False

    def render(
        self,
        pages: _ty.List[Page],
        out: _ty.Union[str, "_os.PathLike[str]"],
        *,
        font: _ty.Optional[str] = None,
        font_size: float = 11.0,
    ) -> None:
        try:
            import docx
            from docx.enum.text import WD_BREAK
            from docx.oxml.ns import qn
            from docx.shared import Pt
        except ImportError as exc:
            raise RuntimeError(
                "Word output needs the 'python-docx' backend; install glyphive[docx]"
            ) from exc
        if font_size <= 0:
            raise ValueError("font_size must be > 0")
        family = font or DEFAULT_DOCX_FONT
        size = Pt(font_size)
        document = docx.Document()
        normal = document.styles["Normal"]
        normal.font.name = family
        normal.font.size = size
        rpr = normal.element.get_or_add_rPr()
        rfonts = rpr.get_or_add_rFonts()
        rfonts.set(qn("w:eastAsia"), family)
        for page_index, page in enumerate(pages):
            if page_index > 0:
                document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            for line in page.text_lines:
                para = document.add_paragraph()
                fmt = para.paragraph_format
                fmt.space_before = Pt(0)
                fmt.space_after = Pt(0)
                fmt.line_spacing = 1.0
                run = para.add_run(line)
                run.font.name = family
                run.font.size = size
                rpr = run._element.get_or_add_rPr()
                rfonts = rpr.get_or_add_rFonts()
                for slot in ("ascii", "hAnsi", "cs", "eastAsia"):
                    rfonts.set(qn(f"w:{slot}"), family)
        buffer = _io.BytesIO()
        document.save(buffer)
        Path(_os.fspath(out)).write_bytes(buffer.getvalue())
