"""PDF renderer with a lazy fpdf2 import."""

from __future__ import annotations

import os as _os
import typing as _ty

from pathlib_next import Path

from glyphive.layout import Page
from glyphive.render._base import DEFAULT_PDF_FONT, RenderFormat

_CORE_FONTS = frozenset({"courier", "helvetica", "times", "symbol", "zapfdingbats", "arial"})


class PdfRenderFormat(RenderFormat):
    name = "pdf"

    @classmethod
    def is_available(cls) -> bool:
        try:
            import importlib.util

            return importlib.util.find_spec("fpdf") is not None
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
            import fpdf
        except ImportError as exc:
            raise RuntimeError(
                "PDF output needs the 'fpdf2' backend; install glyphive[pdf]"
            ) from exc
        if font_size <= 0:
            raise ValueError("font_size must be > 0")
        family = font or DEFAULT_PDF_FONT
        if family.lower() not in _CORE_FONTS:
            supported = ", ".join(sorted(_CORE_FONTS))
            raise ValueError(
                f"unsupported PDF core font {family!r}; choose one of {supported}. "
                "Custom font files are not supported yet."
            )
        pdf = fpdf.FPDF(orientation="P", unit="pt", format="Letter")
        pdf.set_auto_page_break(auto=False)
        pdf.set_margins(36.0, 36.0)
        pdf.set_font(family, size=font_size)
        leading = font_size * 1.2
        for page in pages:
            pdf.add_page()
            pdf.set_xy(36.0, 36.0)
            for line in page.text_lines:
                pdf.set_x(36.0)
                pdf.cell(w=0, h=leading, text=line, new_x="LMARGIN", new_y="NEXT")
        Path(_os.fspath(out)).write_bytes(bytes(pdf.output()))
