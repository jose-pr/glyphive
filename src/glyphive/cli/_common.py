"""Small helpers shared by the glyphive CLI commands."""

from __future__ import annotations

import typing as _ty

from pathlib_next import Path

__all__ = [
    "format_selector_error",
    "load_image_lines",
    "load_transcript_lines",
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


def load_image_lines(
    source: _ty.Union[str, "Path"], *, engine: _ty.Optional[str] = None
) -> _ty.List[str]:
    """OCR one image or a directory of images through one provider instance."""
    from ..restore import ocr

    pages = ocr.ocr_pages(_input_files(source), engine=engine)
    return [line for page in pages for line in page]


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
    """Log recoverable page-integrity warnings emitted by the decoder."""
    for warning in meta.get("_page_warnings", []) or []:
        logger.warning("page integrity warning: %s", warning)
