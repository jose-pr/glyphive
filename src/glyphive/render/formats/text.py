"""Plain-text renderer."""

from __future__ import annotations

import os as _os
import typing as _ty

from pathlib_next import Path

from glyphive.layout import Page
from glyphive.render._base import RenderFormat
from glyphive.render._base import DEFAULT_PAGE_MARGIN_PT

FORM_FEED = "\f"


class TextRenderFormat(RenderFormat):
    name = "text"

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
        del font, font_size, page_margin_pt, horizontal_alignment, character_spacing_pt
        with Path(_os.fspath(out)).open("w", encoding="utf-8", newline="") as stream:
            first = True
            for page in pages:
                if not first:
                    stream.write(FORM_FEED)
                stream.write("\n".join(page.text_lines))
                stream.write("\n")
                first = False
