"""Small helpers shared by the glyphive CLI commands."""

from __future__ import annotations

import io
import typing as _ty
import zipfile

from pathlib_next import Path

__all__ = [
    "format_selector_error",
    "load_image_lines",
    "load_input_lines",
    "load_qr_lines",
    "load_transcript_lines",
    "progress_logger",
    "resolve_destination",
    "warn_page_integrity",
]


def format_selector_error(
    kind: str,
    name: str,
    registered: _ty.Iterable[str],
    *,
    available: _ty.Optional[_ty.Iterable[str]] = None,
    extra: _ty.Optional[str] = None,
) -> str:
    """Format a consistent unknown/unavailable selector diagnostic."""
    registered_names = sorted(str(item) for item in registered)
    if name not in registered_names:
        choices = ", ".join(registered_names) or "(none)"
        return f"unknown {kind} {name!r}; registered {kind}s: {choices}"
    if available is not None and name not in set(str(item) for item in available):
        message = f"{kind} {name!r} is registered but unavailable"
        if extra:
            message += f"; install {extra}"
        return message
    return f"invalid {kind} selector {name!r}"


def resolve_destination(directory: _ty.Optional[str]) -> "Path":
    """Resolve the ``-C`` destination, defaulting to the current directory."""
    return Path(directory) if directory else Path(".")


def load_transcript_lines(source: _ty.Union[str, "Path"]) -> _ty.List[str]:
    """Read one transcript or a directory of transcripts into logical lines."""
    lines: _ty.List[str] = []
    for path in _input_files(source):
        text = path.read_text(encoding="utf-8")
        lines.extend(text.replace("\f", "\n").splitlines())
    return lines


def _blur_images(
    paths: _ty.Sequence["Path"], radius: float, temp_dir: str
) -> _ty.List["Path"]:
    """Return copies of ``paths`` pre-blurred by ``radius`` into ``temp_dir``.

    Raw phone photos are frequently too sharp/noisy for the frame CRC/RS to
    recover; a light Gaussian blur (~0.6 measured best on real scans) softens
    the glyph edges enough for OCR to read them consistently. ``radius <= 0``
    returns the originals unchanged.
    """
    if radius <= 0:
        return list(paths)
    from PIL import Image, ImageFilter

    blurred: _ty.List["Path"] = []
    # Radius is part of the filename so multiple sweep passes into the same
    # temp dir do not clobber each other.
    tag = f"r{radius:g}".replace(".", "_")
    for index, path in enumerate(paths):
        image = Image.open(io.BytesIO(path.read_bytes()))
        image = image.filter(ImageFilter.GaussianBlur(radius=radius))
        out = Path(temp_dir) / f"descan-{tag}-{index:04d}.png"
        image.save(str(out), format="PNG")
        blurred.append(out)
    return blurred


def _normalize_blur(blur: "_ty.Union[float, _ty.Sequence[float]]") -> _ty.List[float]:
    """Coerce a blur radius (or list of radii) into a de-duplicated ordered list."""
    radii = [float(blur)] if isinstance(blur, (int, float)) else [float(r) for r in blur]
    if not radii:
        radii = [0.0]
    seen: _ty.List[float] = []
    for r in radii:
        if r not in seen:
            seen.append(r)
    return seen


def _merge_ocr_lines(
    line_lists: _ty.Iterable[_ty.Sequence[str]],
) -> _ty.List[str]:
    """Union OCR lines across multiple passes, keeping one CRC-valid line per frame.

    Different blur passes recover different lines, and the per-line CRC is the
    correctness oracle -- so combining passes can only add real data. But naive
    unioning also adds *spurious* lines: two passes may read the same frame
    index into different (both-CRC-failing, or one valid one not) strings, and
    feeding several lines at one index to the codec breaks its data/parity
    line-count bookkeeping. So dedupe per (kind, index): for each frame index,
    keep the first CRC-*valid* reading seen (authoritative), or, if no pass read
    it validly, keep one representative so the codec still sees it as an
    erasure. Non-frame lines (headers/footers/noise) pass through de-duplicated
    by exact text, since layout parses those structurally.
    """
    from ..codec.base16c import _parse_line

    passes = [list(lines) for lines in line_lists]
    if len(passes) <= 1:
        return passes[0] if passes else []

    # Which CRC-valid (kind, index) frames does the FIRST pass already have?
    # Line order matters to ``layout.read_pages`` (footers drive page
    # attribution), so the first pass is kept verbatim as the ordered spine;
    # later passes only *append* frames the spine is missing or only read as a
    # CRC-failure, so extra passes can add recovery without disturbing the
    # page structure the first pass established.
    spine = passes[0]
    have_valid: "_ty.Set[_ty.Tuple[str, int]]" = set()
    for line in spine:
        parsed = _parse_line(line)
        if parsed is not None and parsed.ok:
            have_valid.add((parsed.kind, parsed.idx))

    extra: _ty.List[str] = []
    added: "_ty.Set[_ty.Tuple[str, int]]" = set()
    for lines in passes[1:]:
        for line in lines:
            parsed = _parse_line(line)
            if parsed is None or not parsed.ok:
                continue  # only CRC-valid frames from later passes are trusted
            key = (parsed.kind, parsed.idx)
            if key in have_valid or key in added:
                continue
            added.add(key)
            extra.append(line)
    return spine + extra


def load_image_lines(
    source: _ty.Union[str, "Path"],
    *,
    engine: _ty.Optional[str] = None,
    blur: "_ty.Union[float, _ty.Sequence[float]]" = 0.0,
) -> _ty.List[str]:
    """OCR one image or a directory of images through one provider instance.

    ``blur`` is a Gaussian pre-blur radius, or a sequence of radii to try. When
    several radii are given, each image is OCR'd at every radius and the
    CRC-valid lines are merged (see :func:`_merge_ocr_lines`) -- different
    blurs recover different lines. ``0`` (the default) leaves images untouched.
    """
    from tempfile import TemporaryDirectory

    from ..restore import ocr

    images = _input_files(source)
    radii = _normalize_blur(blur)
    per_pass: _ty.List[_ty.List[str]] = []
    with TemporaryDirectory(prefix="glyphive-descan-") as temp:
        for radius in radii:
            candidates = _blur_images(images, radius, temp) if radius > 0 else images
            pages = ocr.ocr_pages(candidates, engine=engine)
            per_pass.append([line for page in pages for line in page])
    return _merge_ocr_lines(per_pass)


def load_qr_lines(source: _ty.Union[str, "Path"]) -> _ty.List[str]:
    """Decode a GQ1 QR image or image directory into exact transcript lines."""
    from ..restore import transcript_from_images

    transcript = transcript_from_images(source)
    try:
        text = transcript.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "decoded Glyphive QR transcript is not valid UTF-8; the symbol set "
            "may belong to a different transport"
        ) from exc
    return text.replace("\f", "\n").splitlines()


def load_input_lines(
    source: _ty.Union[str, "Path"],
    *,
    engine: _ty.Optional[str] = None,
    blur: "_ty.Union[float, _ty.Sequence[float]]" = 0.0,
) -> _ty.List[str]:
    """Read transcripts and OCR images/PDFs/DOCX files based on extension.

    ``blur`` is a Gaussian pre-blur radius or a sequence of radii; each image
    and rasterized-PDF page is OCR'd at every radius and the CRC-valid lines
    are merged across passes (different blurs recover different lines). It
    never affects text transcripts or DOCX. ``0`` (the default) is a single
    no-blur pass.
    """
    from tempfile import TemporaryDirectory

    from ..restore import ocr
    from ..restore.document_images import read_docx_lines, render_document_images

    image_suffixes = {
        ".bmp",
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }
    document_suffixes = {".docx", ".pdf"}
    radii = _normalize_blur(blur)
    lines: _ty.List[str] = []
    with TemporaryDirectory(prefix="glyphive-input-") as temp:
        for index, path in enumerate(_input_files(source)):
            kind = _input_kind(path, image_suffixes, document_suffixes)
            if kind == "pdf":
                passes = []
                for r_index, radius in enumerate(radii):
                    pages = render_document_images(
                        path, Path(temp) / f"{index}-{r_index}", blur=radius
                    )
                    passes.append(
                        [
                            line
                            for page in ocr.ocr_pages(pages, engine=engine)
                            for line in page
                        ]
                    )
                lines.extend(_merge_ocr_lines(passes))
            elif kind == "docx":
                lines.extend(read_docx_lines(path))
            elif kind == "image":
                passes = []
                for radius in radii:
                    candidates = (
                        _blur_images([path], radius, temp) if radius > 0 else [path]
                    )
                    passes.append(
                        [
                            line
                            for page in ocr.ocr_pages(candidates, engine=engine)
                            for line in page
                        ]
                    )
                lines.extend(_merge_ocr_lines(passes))
            else:
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(
                        f"cannot detect supported input type for {path}; expected "
                        "a UTF-8 transcript, image, PDF, or DOCX"
                    ) from exc
                lines.extend(text.replace("\f", "\n").splitlines())
    return lines


def _input_kind(
    path: "Path", image_suffixes: _ty.Set[str], document_suffixes: _ty.Set[str]
) -> str:
    """Classify by magic bytes first and filename extension second."""
    with path.open("rb") as stream:
        prefix = stream.read(16)
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if prefix.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(str(path)) as archive:
                if "word/document.xml" in archive.namelist():
                    return "docx"
        except (OSError, zipfile.BadZipFile):
            pass
    image_magic = (
        prefix.startswith(b"\x89PNG\r\n\x1a\n")
        or prefix.startswith(b"\xff\xd8\xff")
        or prefix.startswith((b"GIF87a", b"GIF89a"))
        or prefix.startswith(b"BM")
        or prefix.startswith((b"II*\x00", b"MM\x00*"))
        or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP")
    )
    if image_magic:
        return "image"
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix in image_suffixes:
        return "image"
    return "text"


def _input_files(source: _ty.Union[str, "Path"]) -> _ty.List["Path"]:
    """Expand a file or its directory's direct child files in stable order."""
    path = Path(source)
    if not path.is_dir():
        return [path]
    files = sorted((child for child in path.glob("*") if child.is_file()), key=str)
    if not files:
        raise ValueError(f"input directory contains no files: {path}")
    return files


def warn_page_integrity(logger: _ty.Any, meta: _ty.Mapping[str, _ty.Any]) -> None:
    """Log recoverable page-integrity warnings emitted by the decoder.

    (Unreadable-index diagnostics are logged inside ``decode_document_to_spool``
    itself, before decode can fail, so they surface even on an RS-budget error.)
    """
    for warning in meta.get("_page_warnings", []) or []:
        logger.warning("page integrity warning: %s", warning)


def progress_logger(
    logger: _ty.Any, *, every: int = 200
) -> _ty.Callable[..., None]:
    """Return an ``on_progress(event, **fields)`` callback that logs sparsely.

    Logs the first occurrence of each event kind, then every ``every``-th
    occurrence of that same kind, and always the final one (``count ==
    total`` when ``total`` is supplied) -- so a large restore doesn't flood
    the log with one line per file, but still reports start/progress/finish.
    """
    counts: _ty.Dict[str, int] = {}

    def report(event: str, **fields: _ty.Any) -> None:
        seen = counts[event] = counts.get(event, 0) + 1
        total = fields.get("total")
        is_last = total is not None and fields.get("count") == total
        if seen == 1 or is_last or seen % every == 0:
            detail = ", ".join(f"{key}={value}" for key, value in fields.items())
            logger.info("%s (%s)", event, detail)

    return report
