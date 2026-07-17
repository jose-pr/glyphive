"""Render document pages to images for OCR and troubleshooting."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import typing as _ty
import zipfile

from pathlib_next import Path

__all__ = ["render_document_images"]


def render_document_images(
    source: _ty.Union[str, "Path"],
    destination: _ty.Union[str, "Path"],
    *,
    dpi: int = 300,
    blur: float = 0.0,
) -> _ty.List["Path"]:
    """Render a PDF or DOCX to ordered PNG files.

    DOCX layout is delegated to LibreOffice because ``python-docx`` reads and
    writes document structure but does not provide a page-layout renderer.
    """
    source_path = Path(source)
    destination_path = Path(destination)
    if dpi <= 0:
        raise ValueError("dpi must be greater than zero")
    if blur < 0:
        raise ValueError("blur must be zero or greater")
    kind = _document_kind(source_path)
    if kind == "pdf":
        return _render_pdf(source_path, destination_path, dpi=dpi, blur=blur)
    if kind == "docx":
        with tempfile.TemporaryDirectory(prefix="glyphive-docx-") as temp:
            pdf = _convert_docx_to_pdf(source_path, Path(temp))
            return _render_pdf(pdf, destination_path, dpi=dpi, blur=blur)
    raise ValueError(
        f"unsupported document input {source_path}; expected .pdf or .docx"
    )


def _document_kind(source: "Path") -> str:
    with source.open("rb") as stream:
        prefix = stream.read(8)
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if prefix.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(str(source)) as archive:
                if "word/document.xml" in archive.namelist():
                    return "docx"
        except (OSError, zipfile.BadZipFile):
            pass
    suffix = source.suffix.lower()
    return suffix[1:] if suffix in {".docx", ".pdf"} else "unknown"


def _convert_docx_to_pdf(source: "Path", destination: "Path") -> "Path":
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if executable is None:
        raise RuntimeError(
            "DOCX page rendering requires LibreOffice (the libreoffice/soffice "
            "command); install LibreOffice and ensure it is on PATH"
        )
    result = subprocess.run(
        [
            executable,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(destination),
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    pdf = destination / f"{source.stem}.pdf"
    if result.returncode or not pdf.is_file():
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            "LibreOffice could not render DOCX to PDF"
            + (f": {detail}" if detail else "")
        )
    return pdf


def _render_pdf(
    source: "Path", destination: "Path", *, dpi: int, blur: float
) -> _ty.List["Path"]:
    try:
        import pypdfium2
        from PIL import ImageFilter
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "PDF page rendering requires pypdfium2 and Pillow; install "
            "glyphive[document-input]"
        ) from exc
    destination.mkdir(parents=True, exist_ok=True)
    document = pypdfium2.PdfDocument(str(source))
    outputs: _ty.List["Path"] = []
    try:
        for index in range(len(document)):
            image = document[index].render(scale=dpi / 72).to_pil().convert("RGB")
            if blur:
                image = image.filter(ImageFilter.GaussianBlur(radius=blur))
            output = destination / f"{source.stem}-{index + 1:04d}.png"
            image.save(str(output), format="PNG", dpi=(dpi, dpi))
            outputs.append(output)
    finally:
        document.close()
    return outputs
