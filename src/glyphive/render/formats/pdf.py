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
_BUNDLED_FONTS = {
    "ocr-b": ("glyphive.assets.fonts.ocr_b", "OCR-B.ttf"),
    "dejavu-sans-mono": (
        "glyphive.assets.fonts.dejavu_sans_mono",
        "DejaVuSansMono.ttf",
    ),
}
_FRAME_KINDS = "HLPQT"

#: Font-file extensions we can hand to FPDF's ``add_font``.
_SYSTEM_FONT_SUFFIXES = frozenset({".ttf", ".otf"})


def _system_font_dirs() -> "_ty.List[Path]":
    """OS font-store directories to search, most-specific (user) first.

    Kept dependency-free on purpose (the project gates every optional import):
    a filesystem scan matches a family name to a bundled/installed font file
    without pulling in ``fonttools``/``matplotlib`` for a glob.
    """
    dirs: _ty.List[Path] = []

    def add(raw: _ty.Optional[str], *parts: str) -> None:
        if raw:
            dirs.append(Path(raw, *parts))

    if _os.name == "nt":
        add(_os.environ.get("LOCALAPPDATA"), "Microsoft", "Windows", "Fonts")
        add(_os.environ.get("WINDIR") or r"C:\Windows", "Fonts")
    else:
        home = _os.path.expanduser("~")
        # User dirs first so a user override wins over a system copy.
        add(home, ".fonts")
        add(_os.environ.get("XDG_DATA_HOME") or _os.path.join(home, ".local", "share"), "fonts")
        add(home, "Library", "Fonts")  # macOS user
        for root in ("/Library/Fonts", "/System/Library/Fonts",
                     "/usr/local/share/fonts", "/usr/share/fonts"):
            add(root)
    return dirs


def _find_system_font(name: str) -> "_ty.Optional[Path]":
    """Locate a ``.ttf``/``.otf`` in the OS font stores whose file stem matches
    ``name`` case-insensitively (e.g. ``"DejaVu Sans Mono"`` or ``"Consolas"``).

    Filename-stem matching, not true family-table resolution — good enough to
    resolve the common case (the file is named after its family) without a font
    library. Returns the first match, or ``None``.
    """
    target = name.strip().lower()
    # Also try a spaceless variant: many font files drop spaces (DejaVuSansMono).
    target_nospace = target.replace(" ", "")

    def match_in(directory: "Path") -> "_ty.Optional[Path]":
        try:
            if not directory.is_dir():
                return None
            entries = sorted(directory.iterdir())
        except OSError:
            return None
        subdirs: _ty.List[Path] = []
        for entry in entries:
            try:
                if entry.is_dir():
                    subdirs.append(entry)
                    continue
                if entry.suffix.lower() not in _SYSTEM_FONT_SUFFIXES:
                    continue
            except (OSError, ValueError):
                continue
            stem = entry.stem.lower()
            if stem == target or stem.replace(" ", "") == target_nospace:
                return entry
        # One level of nesting (Linux groups fonts in per-family subdirs).
        for sub in subdirs:
            hit = _shallow_match(sub, target, target_nospace)
            if hit is not None:
                return hit
        return None

    for directory in _system_font_dirs():
        hit = match_in(directory)
        if hit is not None:
            return hit
    return None


def _shallow_match(
    directory: "Path", target: str, target_nospace: str
) -> "_ty.Optional[Path]":
    """Match font files directly inside ``directory`` (no further recursion)."""
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return None
    for entry in entries:
        try:
            if entry.is_dir() or entry.suffix.lower() not in _SYSTEM_FONT_SUFFIXES:
                continue
        except (OSError, ValueError):
            continue
        stem = entry.stem.lower()
        if stem == target or stem.replace(" ", "") == target_nospace:
            return entry
    return None
_SAFE_ALPHABET = "ABCDHKLMPRTVXY34"

#: Every published OCR-safety measurement in this project (see Known Facts in
#: .agents/plans/codec_naming_and_ocr_safe_index.md) was taken at a 60-character
#: payload row, including the OCR-B "dense" preset -- OCR-B was measured
#: *denser per page* only via a smaller font/DPI at that SAME 60-char width,
#: never via a wider row. Geometric fit alone (fpdf2's glyph-width measurement)
#: says OCR-B 6pt fits ~90 chars/row, but that width was never OCR-tested and
#: real-content testing (2026-07-17 gallery run) found it measurably less
#: reliable than 60. Cap auto-selection at the one width this project has
#: actual evidence for; --line-width still lets a caller opt into an
#: unmeasured wider row explicitly.
_MEASURED_SAFE_LINE_WIDTH = 60


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
        # Distinct FPDF family per bundled font (was hardcoded "OCR-B", which
        # would collide once a second bundled font was added).
        family = lowered
        with resources.as_file(resource) as font_path:
            pdf.add_font(family, "", str(font_path))
            yield family
        return

    candidate = Path(requested)
    if candidate.is_file() and candidate.suffix.lower() in _SYSTEM_FONT_SUFFIXES:
        family = candidate.stem
        pdf.add_font(family, "", str(candidate))
        yield family
        return

    # Not core, not bundled, not an explicit file path: try to resolve the name
    # against the OS font stores before giving up.
    found = _find_system_font(requested)
    if found is not None:
        family = found.stem
        pdf.add_font(family, "", str(found))
        yield family
        return

    supported = ", ".join(sorted(_CORE_FONTS | set(_BUNDLED_FONTS)))
    raise ValueError(
        f"unsupported PDF font {requested!r}; choose one of {supported}, pass an "
        "existing .ttf/.otf file, or install a system font of that name "
        "(searched the OS font stores and found none)"
    )


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

    def geometric_payload_capacity(
        self,
        *,
        font: _ty.Optional[str] = None,
        font_size: float = 11.0,
        page_margin_pt: float = DEFAULT_PAGE_MARGIN_PT,
        character_spacing_pt: float = 0.0,
        nsym_line: int = 2,
    ) -> _ty.Optional[int]:
        """Public hook for the uncapped physical fit (see the base class).

        Delegates to :meth:`_geometric_payload_capacity`; kept as a thin public
        override so the CLI (``--line-width max``) never reaches into a private
        method and the geometry math stays in one place. ``nsym_line`` (default
        2) must match what will actually be encoded (``create --line-parity``)
        so the reserved width for the optional line-parity field is correct.
        """
        return self._geometric_payload_capacity(
            font=font,
            font_size=font_size,
            page_margin_pt=page_margin_pt,
            character_spacing_pt=character_spacing_pt,
            nsym_line=nsym_line,
        )

    def _geometric_payload_capacity(
        self,
        *,
        font: _ty.Optional[str] = None,
        font_size: float = 11.0,
        page_margin_pt: float = DEFAULT_PAGE_MARGIN_PT,
        character_spacing_pt: float = 0.0,
        nsym_line: int = 2,
    ) -> int:
        """Largest payload width that geometrically fits, uncapped.

        Purely a glyph-width measurement -- it has no notion of what has
        actually been OCR-tested. ``payload_capacity`` (the public API) clamps
        this to ``_MEASURED_SAFE_LINE_WIDTH``; this method exists separately so
        the geometry math itself (font size / margins / spacing scaling) can
        be tested without the clamp hiding a regression in the underlying
        measurement.

        ``nsym_line`` (default 2, matching ``create``'s default) reserves room
        for the optional per-line Reed-Solomon parity field
        (:func:`glyphive.codec.base16c._line_parity_chars`): that field is an
        extra glyph run printed between the payload and the check field, so a
        geometric fit that ignored it could choose a payload width whose full
        printed line (label + payload + line-parity + check) overflows the
        page -- exactly the frame-overflow error ``render`` raises loud rather
        than silently shrinking.
        """
        import fpdf

        from glyphive.codec.base16c import BASE16G, _line_parity_chars

        if font_size <= 0:
            raise ValueError("font_size must be > 0")
        available = 612.0 - 2.0 * page_margin_pt
        if available <= 0:
            raise ValueError("page_margin_pt must leave positive printable width")
        if character_spacing_pt < 0:
            raise ValueError("character_spacing_pt must be >= 0")
        if nsym_line not in (0, 2, 4):
            raise ValueError("nsym_line must be 0, 2, or 4")
        line_parity_chars = _line_parity_chars(nsym_line, BASE16G)
        pdf = fpdf.FPDF(orientation="P", unit="pt", format="Letter")
        with registered_pdf_font(pdf, font) as family:
            pdf.set_font(family, size=font_size)
            widest_safe = max(pdf.get_string_width(char) for char in _SAFE_ALPHABET)
            widest_kind = max(pdf.get_string_width(char) for char in _FRAME_KINDS)
            extra_spaces = 1 if line_parity_chars else 0
            fixed_width = (
                widest_kind
                + 9 * widest_safe  # five index and four check characters
                + line_parity_chars * widest_safe  # optional line-parity field
                + (2 + extra_spaces) * pdf.get_string_width(" ")
                + pdf.get_string_width("#")
            )
        # A frame with N payload characters has N+13(+line_parity_chars+1 when
        # the line-parity field is present) total characters and one fewer
        # tracking gap than that.
        total_extra = 12 + (line_parity_chars + 1 if line_parity_chars else 0)
        remaining = available - fixed_width - total_extra * character_spacing_pt
        capacity = int(remaining // (widest_safe + character_spacing_pt))
        return max(0, capacity - capacity % 2)

    def payload_capacity(
        self,
        *,
        font: _ty.Optional[str] = None,
        font_size: float = 11.0,
        page_margin_pt: float = DEFAULT_PAGE_MARGIN_PT,
        character_spacing_pt: float = 0.0,
        nsym_line: int = 2,
    ) -> _ty.Optional[int]:
        """Return the largest OCR-measured-safe payload width that also fits.

        Clamped to ``_MEASURED_SAFE_LINE_WIDTH`` (60): every OCR-safety
        measurement in this project was taken at that row width, including
        the OCR-B "dense" preset, and real-content testing found a wider
        geometrically-fitting row (e.g. OCR-B 6pt's ~90-char fit) measurably
        less reliable. Pass an explicit ``--line-width`` to opt into an
        unmeasured wider row. ``nsym_line`` (default 2) reserves room for the
        optional per-line parity field -- see :meth:`_geometric_payload_capacity`.
        """
        capacity = self._geometric_payload_capacity(
            font=font,
            font_size=font_size,
            page_margin_pt=page_margin_pt,
            character_spacing_pt=character_spacing_pt,
            nsym_line=nsym_line,
        )
        capacity = min(capacity, _MEASURED_SAFE_LINE_WIDTH)
        return max(0, capacity - capacity % 2)

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
                    # Machine/data frames (H/L/P/T) must render at the exact
                    # requested size: silently shrinking a frame to fit distorts
                    # its glyphs (hurting OCR) and hides a misconfigured
                    # font/size/margin/line-width combination. Fail loud instead.
                    # Only the display-only ``#!glyphive`` human header is
                    # allowed to overflow-shrink (restore never trusts it).
                    is_frame = line[:1] in _FRAME_KINDS
                    tracked = raw_width + spacing * max(0, len(line) - 1)
                    if is_frame and tracked > available_width + 1e-6:
                        raise ValueError(
                            "a protected frame line overflows the printable width "
                            f"at {font_size}pt (line width {tracked:.1f}pt > "
                            f"{available_width:.1f}pt available). Reduce --font-size, "
                            "widen the page/margins, or lower --line-width; the "
                            "renderer will not silently shrink a frame to fit."
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
