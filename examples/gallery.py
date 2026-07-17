"""Generate a gallery of glyphive create outputs across formats/settings.

Archives this repository's own ``docs/`` tree (a realistic, small/medium
multi-file source already in the repo) through a matrix of format, font,
font-size, compression, and layout combinations documented in
``docs/guides/create.md``. For each combination it creates the document,
round-trips it through ``extract`` into a private restore directory, and
diffs the restored tree byte-for-byte against the source -- so the gallery
doubles as a correctness fixture, not just a set of demo files.

Run after installing the project:

    python examples/gallery.py [--out examples/gallery] [--source docs/guides]

Writes one manifest.md summarizing every combination's page/byte counts and
round-trip result.
"""

from __future__ import annotations

import filecmp
import shutil
import sys
import typing as _ty

import duho
from duho import LoggingArgs
from pathlib_next import Path

from glyphive import cli as _cli
from glyphive.render import lines_per_page_for


class Combo(_ty.NamedTuple):
    name: str
    format: str
    compression: str
    font: "_ty.Optional[str]"
    font_size: float
    extra: "_ty.List[str]"


COMBOS: "_ty.List[Combo]" = [
    Combo("text-default", "text", "gzip", None, 11.0, []),
    Combo("text-none", "text", "none", None, 11.0, []),
    Combo("pdf-courier-8pt-safe", "pdf", "gzip", "courier", 8.0, []),
    Combo("pdf-courier-11pt", "pdf", "gzip", "courier", 11.0, []),
    Combo("pdf-ocrb-6pt-dense", "pdf", "zstd", "ocr-b", 6.0, []),
    Combo(
        "pdf-minimal-margins",
        "pdf",
        "gzip",
        "courier",
        8.0,
        ["--minimal-margins"],
    ),
    Combo(
        "pdf-centered-spaced",
        "pdf",
        "gzip",
        "ocr-b",
        8.0,
        ["--horizontal-alignment", "center", "--character-spacing", "0.2"],
    ),
    Combo("pdf-pinned-line-width", "pdf", "gzip", "courier", 8.0, ["--line-width", "40"]),
    Combo("docx-consolas-10pt", "docx", "gzip", "Consolas", 10.0, []),
    Combo("docx-none-compression", "docx", "none", "Consolas", 11.0, []),
    Combo("pdf-simple-low-redundancy", "pdf", "gzip", "courier", 8.0, ["--simple"]),
]

#: Baseline for "if you just printed the source as plain text, how many pages
#: would that take?" -- NOT glyphive's OCR-safe encoding (60-char safe-alphabet
#: rows plus per-line CRC/RS framing); an ordinary monospace print convention,
#: so the gallery can show real page-count/byte-size savings (or cost) instead
#: of leaving readers to guess whether the printable format is a net win.
_RAW_PRINT_CHARS_PER_LINE = 80
_RAW_PRINT_FONT_SIZE = 11.0


def _raw_print_chars_per_page() -> int:
    return lines_per_page_for(_RAW_PRINT_FONT_SIZE) * _RAW_PRINT_CHARS_PER_LINE


def _raw_print_pages(total_source_bytes: int) -> int:
    chars_per_page = _raw_print_chars_per_page()
    if chars_per_page <= 0:
        return 0
    return -(-total_source_bytes // chars_per_page)  # ceil div


def _source_tree_bytes(source_dir: "Path") -> int:
    total = 0
    for dirpath, _dirnames, filenames in source_dir.walk():
        for name in filenames:
            total += (dirpath / name).stat().st_size
    return total


def _rendered_page_count(doc_path: "Path", fmt: str) -> "_ty.Optional[int]":
    """Best-effort page count of the produced document, read from the file itself."""
    if fmt == "pdf":
        import pypdfium2

        doc = pypdfium2.PdfDocument(str(doc_path))
        try:
            return len(doc)
        finally:
            doc.close()
    if fmt == "text":
        text = doc_path.read_text(encoding="utf-8")
        return text.count("\f") + 1
    if fmt == "docx":
        # python-docx has no reliable physical-page count (that's a rendering
        # concern Word itself resolves); report None rather than a fake number.
        return None
    return None


class Gallery(LoggingArgs):
    """Build a matrix of create/extract example outputs and verify each round-trips."""

    _parsername_ = "gallery"

    out: str = "examples/gallery"
    "Output directory for generated documents and restore checks."
    ("--out",)

    source: str = "docs/guides"
    "Directory to archive as the example source content."
    ("--source",)

    keep_restored: bool = False
    "Keep each combination's restored-tree copy instead of deleting it after diff."
    ("--keep-restored",)

    def __call__(self) -> int:
        out_dir = Path(self.out)
        source_dir = Path(self.source)
        if not source_dir.is_dir():
            raise SystemExit(f"error: source directory not found: {source_dir}")

        out_dir.mkdir(parents=True, exist_ok=True)
        rows: "_ty.List[_ty.Dict[str, _ty.Any]]" = []
        source_bytes = _source_tree_bytes(source_dir)
        raw_print_pages = _raw_print_pages(source_bytes)
        self._logger_.info(
            "source: %d byte(s) across docs; raw plain-text print baseline "
            "(~%d chars/page at %spt): ~%d page(s)",
            source_bytes,
            _raw_print_chars_per_page(),
            _RAW_PRINT_FONT_SIZE,
            raw_print_pages,
        )

        for combo in COMBOS:
            ext = {"text": ".txt", "pdf": ".pdf", "docx": ".docx"}[combo.format]
            doc_path = out_dir / f"{combo.name}{ext}"
            restore_dir = out_dir / f"{combo.name}-restored"
            if restore_dir.exists():
                shutil.rmtree(restore_dir)

            argv = [
                "create",
                "-f",
                str(doc_path),
                "--format",
                combo.format,
                "--compression",
                combo.compression,
                "-C",
                str(source_dir),
                ".",
            ]
            if combo.font:
                argv += ["--font", combo.font]
            argv += ["--font-size", str(combo.font_size)]
            argv += combo.extra

            self._logger_.info("building %s: %s", combo.name, " ".join(argv))
            try:
                rc = _cli.run(argv)
            except Exception as exc:  # noqa: BLE001 - report, don't abort the gallery
                rows.append({"combo": combo.name, "status": f"CREATE ERROR: {exc}"})
                continue
            if rc != 0:
                rows.append({"combo": combo.name, "status": f"CREATE FAILED (rc={rc})"})
                continue

            byte_size = doc_path.stat().st_size
            pages = _rendered_page_count(doc_path, combo.format)
            page_ratio = (
                f"{pages / raw_print_pages:.1f}x"
                if pages is not None and raw_print_pages
                else "n/a"
            )
            row: "_ty.Dict[str, _ty.Any]" = {
                "combo": combo.name,
                "format": combo.format,
                "font": combo.font or "(default)",
                "font_size": combo.font_size,
                "compression": combo.compression,
                "bytes": byte_size,
                "pages": pages if pages is not None else "n/a",
                "vs_raw_print": page_ratio,
            }

            # PDF/DOCX input is always rasterized/OCR'd on extract, even for a
            # locally-generated file with no scan/print step (glyphive treats any
            # PDF/DOCX as "possibly physical"; there is no raw-text-layer
            # shortcut) -- so a PDF/DOCX round-trip here is a real OCR gate, not
            # a lossless format check. A setting the docs call OCR-fragile (e.g.
            # 11pt Courier vs. the 8pt "safe" default) can genuinely fail this
            # gate on real content; that is itself useful, honest signal for the
            # gallery to record rather than a script bug to hide.
            try:
                rc = _cli.run(["extract", "-f", str(doc_path), "-C", str(restore_dir)])
            except Exception as exc:  # noqa: BLE001
                row["status"] = f"EXTRACT ERROR: {exc}"
                rows.append(row)
                continue
            if rc != 0:
                row["status"] = f"EXTRACT FAILED (rc={rc})"
                rows.append(row)
                continue

            identical = _trees_equal(source_dir, restore_dir)
            row["status"] = "OK" if identical else "MISMATCH"
            rows.append(row)
            if not self.keep_restored:
                shutil.rmtree(restore_dir, ignore_errors=True)

        manifest = out_dir / "manifest.md"
        _write_manifest(manifest, rows, source_bytes=source_bytes, raw_print_pages=raw_print_pages)
        self._logger_.info("wrote %d combination(s); manifest: %s", len(rows), manifest)

        failures = [r for r in rows if r["status"] != "OK"]
        if failures:
            self._logger_.error("%d combination(s) did not round-trip cleanly", len(failures))
            return 1
        return 0


def _relative_files(root: "Path") -> "_ty.List[str]":
    found: "_ty.List[str]" = []
    for dirpath, _dirnames, filenames in root.walk():
        for name in filenames:
            found.append(str((dirpath / name).relative_to(root)))
    return sorted(found)


def _trees_equal(left: "Path", right: "Path") -> bool:
    left_files = _relative_files(left)
    right_files = _relative_files(right)
    if left_files != right_files:
        return False
    _match, mismatch, errors = filecmp.cmpfiles(
        str(left), str(right), left_files, shallow=False
    )
    return not mismatch and not errors


def _write_manifest(
    path: "Path",
    rows: "_ty.List[_ty.Dict[str, _ty.Any]]",
    *,
    source_bytes: int,
    raw_print_pages: int,
) -> None:
    lines = [
        "# Glyphive example gallery",
        "",
        "Generated by `examples/gallery.py`. Each row is one `create`/`extract`",
        "round-trip through the library, diffed byte-for-byte against the source.",
        "",
        f"Source: **{source_bytes} bytes** across the archived docs. Printed as "
        f"plain monospace text with no encoding at all (80 chars/line, 11pt, "
        f"US Letter), that content alone would take roughly "
        f"**{raw_print_pages} page(s)**. The `pages` and `vs raw print` columns "
        "below compare each combination's actual glyphive page count against "
        "that baseline -- a ratio near or above 1x means the OCR-safety framing "
        "(measured-safe alphabet, per-line CRC, interleaved Reed-Solomon) is "
        "costing about as much or more space than the content itself, which is "
        "expected: this format trades density for scan/OCR survivability, not "
        "the other way around. Use `--simple` (or a lower `--parity-ratio`) "
        "when that trade isn't worth it for small, disposable, or easily "
        "re-typeable documents.",
        "",
        "**A `pdf-*` row's real-OCR round-trip is not guaranteed to reproduce "
        "run to run when the source content changes even slightly, at the "
        "default 12% parity ratio.** Investigating the 2026-07-17 gallery run "
        "found the encoding genuinely sits close to its Reed-Solomon budget on "
        "real varied prose (not the synthetic character grids the plan's "
        "earlier \"measured safe\" claims were based on) -- re-running this "
        "generator after `docs/guides/create.md` changed by a few bytes flipped "
        "which combinations passed and which didn't, including the documented "
        "\"safe\" 8pt Courier default. This is real, open follow-up work "
        "(`.agents/plans/ocr_restore_robustness.md` Phases 4/5): either raise "
        "the default parity ratio, re-measure per-font safety against varied "
        "real content instead of synthetic grids, or both. `EXTRACT ERROR` "
        "rows below are exactly this -- not a bug in this script.",
        "",
        "| combination | status | format | font | size | compression | bytes | pages | vs raw print |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {combo} | {status} | {format} | {font} | {font_size} | "
            "{compression} | {bytes} | {pages} | {vs_raw_print} |".format(
                combo=row.get("combo", ""),
                status=row.get("status", ""),
                format=row.get("format", ""),
                font=row.get("font", ""),
                font_size=row.get("font_size", ""),
                compression=row.get("compression", ""),
                bytes=row.get("bytes", ""),
                pages=row.get("pages", "n/a"),
                vs_raw_print=row.get("vs_raw_print", "n/a"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(duho.main(Gallery))
