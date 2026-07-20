"""The ``glyphive inspect`` command — a read-only recovery-headroom report.

Unlike ``list`` (which fully decodes and prints the file manifest), ``inspect``
reads only the protected header + footers (:func:`glyphive.layout.read_pages`)
and reports how much damage the document can survive: dedicated whole-page
parity (K pages), the per-line Reed-Solomon budget (realized ``nsym``), and
which pages are present / missing / reconstructable. It never fully decodes,
never verifies the whole-document SHA, and never writes a file — so it works on
a partially damaged scan a normal restore would reject.
"""

from __future__ import annotations

import json as _json
import typing as _ty

from duho import LoggingArgs
from pathlib_next import Path

from ._common import load_input_lines, load_qr_lines

__all__ = ["Inspect"]


class Inspect(LoggingArgs):
    """Report a document's recovery headroom without extracting or verifying."""

    _parsername_ = "inspect"

    file: str
    "Input file or directory (text, images, PDF, or DOCX; type detected automatically)."
    ("-f", "--file")

    ocr_engine: "_ty.Optional[str]" = None
    "OCR registry provider for image or document input (default: automatic preference)."
    ("--ocr-engine",)

    from_qr: bool = False
    "Decode -f as GQ1 QR page images (requires glyphive[qr])."
    ("--from-qr",)

    json: bool = False
    "Emit the report as a machine-readable JSON object instead of text."
    ("--json",)

    strict: bool = False
    "Exit non-zero when the document is already unrecoverable (missing data "
    "pages beyond what page-parity can rebuild)."
    ("--strict",)

    temp_dir: "_ty.Optional[str]" = None
    "Directory for private spools."
    ("--temp-dir",)

    chunk_size: int = 1024 * 1024
    "Streaming I/O chunk size in bytes."
    ("--chunk-size",)

    def __call__(self) -> int:
        from .. import layout as _layout
        from ..codec import get as _get_codec
        from ..codec.engine import BASE16G, describe_line_stream

        source = Path(self.file)
        lines = (
            load_qr_lines(source)
            if self.from_qr
            else load_input_lines(source, engine=self.ocr_engine)
        )

        try:
            meta, encoded = _layout.read_pages(lines)
        except _layout.MissingPageError as exc:
            # Zero codec lines survived: the document is unreadable at this
            # layer. Report what we can and fail (strict or not — nothing to
            # inspect).
            report = {
                "readable": False,
                "reason": str(exc),
                "missing_pages": list(exc.missing),
                "total_pages": exc.total,
            }
            self._emit(report)
            return 1
        except _layout.LayoutError as exc:
            self._emit({"readable": False, "reason": str(exc)})
            return 1

        # The stream shape must be read with the payload codec's own alphabet
        # and framing; the protected header names the codec. An unknown codec
        # (e.g. a plugin not loaded for this invocation) falls back to the
        # base16g bootstrap spec rather than failing the whole report.
        spec = BASE16G
        codec_name = meta.get("codec")
        if codec_name:
            try:
                spec = getattr(_get_codec(str(codec_name)), "_spec", BASE16G)
            except ValueError:
                pass
        shape = describe_line_stream(encoded, spec)
        data_pages = int(meta["pages"])
        parity_pages = int(meta.get("pgpar", 0) or 0)
        parity_field = int(meta.get("pgpar_field", 8) or 8)
        missing = list(meta.get("_missing_pages", []) or [])
        reconstructed = list(meta.get("_reconstructed_pages", []) or [])
        # A missing data page is reconstructable if page-parity can still cover
        # the total missing count.
        still_missing = [n for n in missing if n not in reconstructed]
        unrecoverable = len(still_missing) > 0 and len(still_missing) > parity_pages

        report = {
            "readable": True,
            "codec": meta.get("codec"),
            "comp": meta.get("comp"),
            "meta": meta.get("meta"),
            "files": meta.get("files"),
            "bytes": meta.get("bytes"),
            "data_pages": data_pages,
            "parity_pages": parity_pages,
            "parity_field": parity_field,
            "survives_lost_data_pages": parity_pages,
            "pages_present": sorted(meta.get("_pages_seen", []) or []),
            "pages_missing": missing,
            "pages_reconstructed": reconstructed,
            "pages_still_missing": still_missing,
            "line_rs_nsym": shape.nsym,
            "line_rs_blocks": shape.nblocks,
            "line_parity_nsym": shape.nsym_line,
            "data_lines": shape.data_lines,
            "parity_lines": shape.parity_lines,
            "page_warnings": list(meta.get("_page_warnings", []) or []),
            "footer_hash_notes": list(meta.get("_footer_hash_notes", []) or []),
        }
        self._emit(report)

        if self.strict and unrecoverable:
            return 2
        return 0

    def _emit(self, report: "_ty.Dict[str, _ty.Any]") -> None:
        if self.json:
            print(_json.dumps(report, indent=2, default=str))
            return
        if not report.get("readable", False):
            print(f"unreadable: {report.get('reason')}")
            return
        nsym = report["line_rs_nsym"]
        nsym_text = (
            f"{nsym} erasure(s)/block over {report['line_rs_blocks']} block(s)"
            if nsym is not None
            else "indeterminate (ambiguous line stream)"
        )
        print(
            "glyphive codec={codec} comp={comp} files={files} bytes={bytes}".format(
                **report
            )
        )
        parity_field_text = (
            f" [GF(2^{report['parity_field']})]" if report["parity_pages"] else ""
        )
        print(
            (
                "  pages: {data_pages} data + {parity_pages} parity"
                + parity_field_text
                + " (survives up to {survives_lost_data_pages} wholly lost data "
                "page(s))"
            ).format(**report)
        )
        print(f"  per-line Reed-Solomon budget: {nsym_text}")
        line_parity = report["line_parity_nsym"]
        line_parity_text = (
            f"{line_parity} parity byte(s)/line (in-line self-heal enabled)"
            if line_parity
            else "0 (in-line self-heal disabled for this document)"
        )
        print(f"  per-line parity field: {line_parity_text}")
        if report["pages_missing"]:
            print(f"  missing pages: {report['pages_missing']}")
        if report["pages_reconstructed"]:
            print(f"  reconstructed from parity: {report['pages_reconstructed']}")
        if report["pages_still_missing"]:
            print(f"  STILL MISSING (unrecoverable): {report['pages_still_missing']}")
        for warning in report["page_warnings"]:
            print(f"  warning: {warning}")
        if report["footer_hash_notes"]:
            print(
                f"  footer-hash advisories: {len(report['footer_hash_notes'])} "
                "page(s) (normal on OCR input; pages still decoded)"
            )
