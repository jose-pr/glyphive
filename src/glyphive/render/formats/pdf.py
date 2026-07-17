"""PDF renderer with a lazy fpdf2 import."""

from __future__ import annotations

from contextlib import contextmanager
from importlib import resources
import os as _os
import typing as _ty

from pathlib_next import Path

from glyphive.layout import Page
from glyphive.render._base import (
    DEFAULT_PAGE_MARGIN_PT,
    DEFAULT_PDF_FONT,
    HORIZONTAL_ALIGNMENTS,
    RenderFormat,
)

_CORE_FONTS = frozenset(
    {"courier", "helvetica", "times", "symbol", "zapfdingbats", "arial"}
)
_BUNDLED_FONTS = {"ocr-b": ("glyphive.assets.fonts.ocr_b", "OCR-B.ttf")}


@contextmanager
def registered_pdf_font(pdf: _ty.Any, font: _ty.Optional[str]):
    """Yield an FPDF family for a core, bundled, or filesystem font."""
    requested = font or DEFAULT_PDF_FONT
    lowered = requested.lower()
    if lowered in _CORE_FONTS:
        yield lowered
        return

    if lowered in _BUNDLED_FONTS:
        package, filename = _BUNDLED_FONTS[lowered]
        resource = resources.files(package).joinpath(filename)
        family = "OCR-B"
        with resources.as_file(resource) as font_path:
            pdf.add_font(family, "", str(font_path))
            yield family
        return

    candidate = Path(requested)
    if not candidate.is_file() or candidate.suffix.lower() not in {".otf", ".ttf"}:
        supported = ", ".join(sorted(_CORE_FONTS | set(_BUNDLED_FONTS)))
        raise ValueError(
            f"unsupported PDF font {requested!r}; choose one of {supported}, "
            "or pass an existing .ttf/.otf file"
        )
    family = candidate.stem
    pdf.add_font(family, "", str(candidate))
    yield family


def _fitted_font_size(
    requested_size: float,
    text_width: float,
    available_width: float,
    *,
    character_spacing_pt: float = 0.0,
    character_count: int = 0,
) -> float:
    """Fit one physical line horizontally without changing its row budget."""
    tracked_width = text_width + character_spacing_pt * max(0, character_count - 1)
    if tracked_width <= available_width:
        return requested_size
    glyph_budget = available_width - character_spacing_pt * max(
        0, character_count - 1
    )
    if glyph_budget <= 0:
        raise ValueError("character spacing leaves no room for line glyphs")
    return requested_size * glyph_budget / text_width


def _line_character_spacing(
    line: str,
    *,
    alignment: str,
    base_spacing_pt: float,
    text_width: float,
    available_width: float,
) -> float:
    """Return fixed tracking, or tracking that distributes a line edge-to-edge."""
    if alignment != "justify" or len(line) < 2:
        return base_spacing_pt
    return max(base_spacing_pt, (available_width - text_width) / (len(line) - 1))


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
        pages: _ty.Iterable[Page],
        out: _ty.Union[str, "_os.PathLike[str]"],
        *,
        font: _ty.Optional[str] = None,
        font_size: float = 11.0,
        page_margin_pt: float = DEFAULT_PAGE_MARGIN_PT,
        horizontal_alignment: str = "left",
        character_spacing_pt: float = 0.0,
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
        if horizontal_alignment not in HORIZONTAL_ALIGNMENTS:
            raise ValueError("horizontal_alignment must be left, center, or justify")
        if character_spacing_pt < 0:
            raise ValueError("character_spacing_pt must be >= 0")
        pdf = fpdf.FPDF(orientation="P", unit="pt", format="Letter")
        pdf.set_auto_page_break(auto=False)
        pdf.set_margins(page_margin_pt, page_margin_pt)
        with registered_pdf_font(pdf, font) as family:
            pdf.set_font(family, size=font_size)
            leading = font_size * 1.2
            for page in pages:
                pdf.add_page()
                pdf.set_xy(page_margin_pt, page_margin_pt)
                for line in page.text_lines:
                    pdf.set_x(page_margin_pt)
                    available_width = pdf.w - 2.0 * page_margin_pt
                    raw_width = pdf.get_string_width(line)
                    spacing = _line_character_spacing(
                        line,
                        alignment=horizontal_alignment,
                        base_spacing_pt=character_spacing_pt,
                        text_width=raw_width,
                        available_width=available_width,
                    )
                    line_size = _fitted_font_size(
                        font_size,
                        raw_width,
                        available_width,
                        character_spacing_pt=spacing,
                        character_count=len(line),
                    )
                    if line_size != font_size:
                        pdf.set_font(family, size=line_size)
                    pdf.set_char_spacing(spacing)
                    pdf.cell(
                        w=available_width,
                        h=leading,
                        text=line,
                        align="C" if horizontal_alignment == "center" else "L",
                        new_x="LMARGIN",
                        new_y="NEXT",
                    )
                    pdf.set_char_spacing(0)
                    if line_size != font_size:
                        pdf.set_font(family, size=font_size)
            pdf.output(str(Path(_os.fspath(out))))
