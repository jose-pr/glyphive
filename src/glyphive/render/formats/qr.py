"""QR and hybrid US-Letter PDF renderers with lazy optional imports."""

from __future__ import annotations

import io
import os as _os
import typing as _ty

from pathlib_next import Path

from glyphive import layout as _layout
from glyphive.layout import Page
from glyphive.render._base import DEFAULT_PAGE_MARGIN_PT, RenderFormat
from glyphive.restore.qr import chunk_transcript, symbol_png, unpack_chunk


def _available() -> bool:
    try:
        import importlib.util

        return all(
            importlib.util.find_spec(name) is not None
            for name in ("fpdf", "segno")
        )
    except Exception:
        return False


def _transcript(pages: _ty.Iterable[Page]) -> _ty.Tuple[bytes, bytes]:
    materialized = list(pages)
    if not materialized:
        raise ValueError("QR output requires at least one Glyphive page")
    text = "\f".join("\n".join(page.text_lines) + "\n" for page in materialized)
    try:
        meta = _layout.parse_header(materialized[0].text_lines[0])
        digest = bytes.fromhex(str(meta["sha256"]))
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("QR output could not read the document digest") from exc
    if len(digest) != 32:
        raise ValueError("QR output requires a 32-byte document digest")
    return text.encode("utf-8"), digest


def _backend() -> _ty.Any:
    try:
        import fpdf
    except ImportError as exc:
        raise RuntimeError(
            "QR PDF output requires 'pip install glyphive[qr,pdf]'"
        ) from exc
    return fpdf


def _new_pdf(fpdf: _ty.Any, margin: float) -> _ty.Any:
    if margin < 0 or margin * 2 >= 612.0:
        raise ValueError("page_margin_pt must leave positive printable width")
    pdf = fpdf.FPDF(orientation="P", unit="pt", format="Letter")
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(margin, margin)
    return pdf


class QrRenderFormat(RenderFormat):
    """Render six authenticated transcript symbols per US-Letter PDF page."""

    name = "qr"

    @classmethod
    def is_available(cls) -> bool:
        return _available()

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
        del font, font_size, horizontal_alignment, character_spacing_pt
        fpdf = _backend()
        transcript, digest = _transcript(pages)
        symbols = chunk_transcript(transcript, digest)
        pdf = _new_pdf(fpdf, page_margin_pt)
        cell_width = (612.0 - 2 * page_margin_pt) / 2
        cell_height = (792.0 - 2 * page_margin_pt) / 3
        image_size = min(cell_width - 16, cell_height - 28)
        if image_size <= 0:
            raise ValueError("page margins leave no room for QR symbols")
        for index, symbol in enumerate(symbols):
            slot = index % 6
            if slot == 0:
                pdf.add_page()
            column, row = slot % 2, slot // 2
            x = page_margin_pt + column * cell_width + (cell_width - image_size) / 2
            y = page_margin_pt + row * cell_height + 18
            pdf.set_font("helvetica", size=7)
            pdf.set_xy(page_margin_pt + column * cell_width, y - 13)
            pdf.cell(
                cell_width,
                9,
                text=f"GQ1 {digest.hex()[:12]} {index + 1}/{len(symbols)}",
                align="C",
            )
            pdf.image(io.BytesIO(symbol_png(symbol)), x=x, y=y, w=image_size, h=image_size)
        pdf.output(str(Path(_os.fspath(out))))


class HybridRenderFormat(RenderFormat):
    """Render one authenticated symbol and its transcript slice per PDF page."""

    name = "hybrid"

    @classmethod
    def is_available(cls) -> bool:
        return _available()

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
        del font, horizontal_alignment, character_spacing_pt
        if font_size <= 0:
            raise ValueError("font_size must be > 0")
        fpdf = _backend()
        transcript, digest = _transcript(pages)
        symbols = chunk_transcript(transcript, digest)
        pdf = _new_pdf(fpdf, page_margin_pt)
        available_width = 612.0 - 2 * page_margin_pt
        qr_size = min(300.0, available_width, 330.0 - page_margin_pt)
        if qr_size <= 0:
            raise ValueError("page margins leave no room for a QR symbol")
        for index, symbol in enumerate(symbols):
            chunk = unpack_chunk(symbol)
            pdf.add_page()
            x = page_margin_pt + (available_width - qr_size) / 2
            pdf.image(
                io.BytesIO(symbol_png(symbol)),
                x=x,
                y=page_margin_pt,
                w=qr_size,
                h=qr_size,
            )
            y = page_margin_pt + qr_size + 12
            pdf.set_xy(page_margin_pt, y)
            pdf.set_font("courier", size=min(font_size, 8.0))
            pdf.multi_cell(
                available_width,
                min(font_size, 8.0) * 1.2,
                text=chunk.payload.decode("utf-8").replace("\f", "[FORM FEED]"),
            )
        pdf.output(str(Path(_os.fspath(out))))
