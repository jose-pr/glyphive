"""Named render formats for already-paginated glyphive pages."""

from __future__ import annotations

import os as _os
import typing as _ty

from ._base import (
    DEFAULT_DOCX_FONT,
    DEFAULT_MONO_FONT,
    DEFAULT_PDF_FONT,
    DEFAULT_PAGE_MARGIN_PT,
    HORIZONTAL_ALIGNMENTS,
    MINIMAL_PAGE_MARGIN_PT,
    RenderFormat,
)
from .formats import DocxRenderFormat, PdfRenderFormat, TextRenderFormat

FORMATS: _ty.Final[_ty.FrozenSet[str]] = frozenset(RenderFormat.names())

__all__ = [
    "FORMATS",
    "DEFAULT_MONO_FONT",
    "DEFAULT_PDF_FONT",
    "DEFAULT_DOCX_FONT",
    "DEFAULT_PAGE_MARGIN_PT",
    "MINIMAL_PAGE_MARGIN_PT",
    "HORIZONTAL_ALIGNMENTS",
    "RenderFormat",
    "available",
    "get",
    "lines_per_page_for",
    "names",
    "render",
]


def get(name: str) -> RenderFormat:
    return RenderFormat.get(name)


def names() -> _ty.List[str]:
    return RenderFormat.names()


def available() -> _ty.List[str]:
    return RenderFormat.available()


def lines_per_page_for(
    font_size: float,
    *,
    page_height_pt: float = 792.0,
    page_margin_pt: float = DEFAULT_PAGE_MARGIN_PT,
) -> int:
    if font_size <= 0:
        raise ValueError("font_size must be > 0")
    usable = page_height_pt - 2.0 * page_margin_pt
    leading = font_size * 1.2
    if leading <= 0 or usable <= 0:
        raise ValueError("page geometry leaves no room for any line")
    return max(3, int(usable // leading))


def render(
    pages: _ty.List["Page"],
    out: _ty.Union[str, "_os.PathLike[str]"],
    fmt: str,
    *,
    font: _ty.Optional[str] = None,
    font_size: float = 11.0,
    page_margin_pt: float = DEFAULT_PAGE_MARGIN_PT,
    horizontal_alignment: str = "left",
    character_spacing_pt: float = 0.0,
) -> None:
    """Resolve ``fmt`` and delegate one time to its registered implementation."""
    get(fmt).render(
        pages,
        out,
        font=font,
        font_size=font_size,
        page_margin_pt=page_margin_pt,
        horizontal_alignment=horizontal_alignment,
        character_spacing_pt=character_spacing_pt,
    )
