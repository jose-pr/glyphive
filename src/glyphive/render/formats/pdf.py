"""PDF renderer with a lazy fpdf2 import."""

from __future__ import annotations

import os as _os
import typing as _ty

from pathlib_next import Path

from glyphive.layout import Page
from glyphive.render._base import (
    DEFAULT_PAGE_MARGIN_PT,
    DEFAULT_PDF_FONT,
    RenderFormat,
)

_CORE_FONTS = frozenset({"courier", "helvetica", "times", "symbol", "zapfdingbats", "arial"})


def _fitted_font_size(
    requested_size: float, text_width: float, available_width: float
) -> float:
    """Fit one physical line horizontally without changing its row budget."""
    if text_width <= available_width:
        return requested_size
    return requested_size * available_width / text_width


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
        page_margin_pt: float = DEFAULT_PAGE_MARGIN_PT,
    ) -> None:
        try:
            import fpdf
        except ImportError as exc:
            raise RuntimeError(
                "PDF output needs the 'fpdf2' backend; install glyphive[pdf]"
            ) from exc
        if font_size <= 0:
            raise ValueError("font_size must be > 0")
        if page_margin_pt < 0 or page_margin_pt * 2 >= 612.0:
            raise ValueError("page_margin_pt must leave positive printable width")
        family = font or DEFAULT_PDF_FONT
        if family.lower() not in _CORE_FONTS:
            supported = ", ".join(sorted(_CORE_FONTS))
            raise ValueError(
                f"unsupported PDF core font {family!r}; choose one of {supported}. "
                "Custom font files are not supported yet."
            )
        pdf = fpdf.FPDF(orientation="P", unit="pt", format="Letter")
        pdf.set_auto_page_break(auto=False)
        pdf.set_margins(page_margin_pt, page_margin_pt)
        pdf.set_font(family, size=font_size)
        leading = font_size * 1.2
        for page in pages:
            pdf.add_page()
            pdf.set_xy(page_margin_pt, page_margin_pt)
            for line in page.text_lines:
                pdf.set_x(page_margin_pt)
                line_size = _fitted_font_size(
                    font_size,
                    pdf.get_string_width(line),
                    pdf.w - 2.0 * page_margin_pt,
                )
                if line_size != font_size:
                    pdf.set_font(family, size=line_size)
                pdf.cell(w=0, h=leading, text=line, new_x="LMARGIN", new_y="NEXT")
                if line_size != font_size:
                    pdf.set_font(family, size=font_size)
        Path(_os.fspath(out)).write_bytes(bytes(pdf.output()))
