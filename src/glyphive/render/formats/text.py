"""Plain-text renderer."""

from __future__ import annotations

import os as _os
import typing as _ty

from pathlib_next import Path

from glyphive.layout import Page
from glyphive.render._base import RenderFormat

FORM_FEED = "\f"


class TextRenderFormat(RenderFormat):
    name = "text"

    def render(
        self,
        pages: _ty.List[Page],
        out: _ty.Union[str, "_os.PathLike[str]"],
        *,
        font: _ty.Optional[str] = None,
        font_size: float = 11.0,
    ) -> None:
        del font, font_size
        page_blocks = ["\n".join(page.text_lines) for page in pages]
        document = FORM_FEED.join(page_blocks)
        if document and not document.endswith("\n"):
            document += "\n"
        Path(_os.fspath(out)).write_text(document, encoding="utf-8", newline="")
