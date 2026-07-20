"""Calibrate the plan-3 OCR confidence threshold against measured Tesseract data.

Reports, for a sweep of candidate thresholds ``t`` in ``0.50..0.95``:

    P(char wrong | conf < t)   -- precision: of the characters this threshold
                                  would flag as "suspect", how many actually
                                  are misreads?
    recall(wrong chars, t)     -- of every ACTUALLY wrong character in the
                                  sample, what fraction had conf < t (i.e.
                                  would have been caught)?

:mod:`glyphive.codec.engine`'s ``decode_spool`` uses a low-confidence
character only to choose ERASURE POSITIONS for a line that has already
failed its CRC -- it is a hint, never an acceptance criterion (the CRC/RS/
SHA-256 gates are unchanged). The calibration goal per plan 3 is a threshold
with >=90% recall of wrong characters -- missing a genuinely bad character
means it enters the document-level Reed-Solomon stream as an unmarked
"blind" error (costing 2x the RS budget of a marked erasure, and only
caught by the two-pass safety valve if its block happens to fail outright),
so recall matters more here than precision; a few extra low-value erasures
are cheap next to a missed one.

Ground truth comes from one of two sources:

1. ``--corpus DIR`` -- a directory of REAL scanned images, each with a
   sibling ``<image>.txt`` ground-truth transcript (one printed line per
   text line, in reading order, using glyphive's own safe alphabet). This is
   the "benchmark scans" corpus the plan describes; none ships in this repo
   today (see the module docstring on why), so this path is exercised once
   a corpus is captured.
2. The default, corpus-free mode: reuses ``tools/ocr_font_report.py``'s
   render/rasterize harness to print known-random lines of glyphive's own
   measured-safe alphabet at a chosen font/size/dpi, so ground truth is
   exact by construction. This is the "``tools/ocr_font_report.py`` harness"
   alternative the plan names explicitly.

Degrades cleanly (prints a clear message, exits 0, writes nothing) when
Tesseract is not installed on this machine -- this tool must never fabricate
calibration numbers or block on an unavailable environment.

Usage::

    python tools/conf_calibration.py --font courier --size 8 --dpi 300 --rows 120
    python tools/conf_calibration.py --corpus benchmarks/scans/ --json out.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version

from pathlib_next import Path

_THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
_RECALL_TARGET = 0.90


def _engine_version() -> "str | None":
    try:
        return version("pytesseract")
    except PackageNotFoundError:
        return None


def _tesseract_available() -> bool:
    from glyphive.restore import ocr as glyphive_ocr

    try:
        return glyphive_ocr.get("tesseract").is_available()
    except Exception:
        return False


def _collect_char_samples_from_ocr_lines(printed: str, ocr_text: str, ocr_conf) -> list:
    """Position-align one printed line against its OCR reading.

    Same convention as ``tools/ocr_font_report.py``: strip spaces (the
    payload alphabet never contains one, so an interior space is provably
    OCR line-wrap noise) and skip a length-mismatched line entirely -- that
    is a distinct failure mode (insertion/deletion), not a per-character
    confidence question. Returns a list of ``(wrong: bool, conf: float|None)``.
    """
    stripped_positions = [i for i, ch in enumerate(ocr_text) if ch != " "]
    compact_ocr = "".join(ocr_text[i] for i in stripped_positions)
    compact_conf = [ocr_conf[i] if ocr_conf else None for i in stripped_positions]
    if len(compact_ocr) != len(printed):
        return []
    samples = []
    for pos, true_char in enumerate(printed):
        got_char = compact_ocr[pos]
        conf = compact_conf[pos]
        samples.append((got_char != true_char, conf))
    return samples


def _samples_from_corpus(corpus_dir: "Path") -> list:
    """Read ``(image, ground_truth.txt)`` pairs and OCR each, returning
    ``(wrong, conf)`` samples across every aligned character."""
    from glyphive.restore import ocr as glyphive_ocr

    provider = glyphive_ocr.get("tesseract")
    image_suffixes = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
    samples: list = []
    images = sorted(p for p in corpus_dir.glob("*") if p.suffix.lower() in image_suffixes)
    if not images:
        raise SystemExit(f"no images found in corpus directory {corpus_dir}")
    for image_path in images:
        truth_path = image_path.with_suffix(".txt")
        if not truth_path.is_file():
            print(f"skipping {image_path.name}: no sibling {truth_path.name}", file=sys.stderr)
            continue
        truth_lines = truth_path.read_text(encoding="utf-8").splitlines()
        ocr_lines = provider.ocr_image(image_path)
        for i, truth in enumerate(truth_lines):
            if i >= len(ocr_lines):
                continue
            samples.extend(
                _collect_char_samples_from_ocr_lines(
                    truth, ocr_lines[i].text, ocr_lines[i].char_conf
                )
            )
    return samples


def _samples_from_synthetic_render(
    font_arg: str, size: float, dpi: int, rows: int, seed: int
) -> list:
    """Render known-random lines of glyphive's safe alphabet and OCR them.

    Reuses ``tools/ocr_font_report.py``'s render/rasterize harness so this
    tool has no independent PDF-layout logic to drift out of sync with it.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ocr_font_report as _report

    from glyphive.codec.engine import ALPHABET
    from glyphive.restore import ocr as glyphive_ocr

    chars_per_line, lines_per_page, _max_w, font_label = _report.font_geometry(
        font_arg, size, ALPHABET
    )
    import random

    rng = random.Random(seed)
    printed_rows = [
        "".join(rng.choice(ALPHABET) for _ in range(chars_per_line)) for _ in range(rows)
    ]

    provider = glyphive_ocr.get("tesseract")
    samples: list = []
    with tempfile.TemporaryDirectory(prefix="conf_calibration_") as tmp:
        scratch = Path(tmp)
        pdf_path = scratch / "sample.pdf"
        _report.render_lines(printed_rows, font_arg, size, pdf_path)

        import pypdfium2

        doc = pypdfium2.PdfDocument(str(pdf_path))
        try:
            ocr_lines = []
            for i in range(len(doc)):
                bitmap = doc[i].render(scale=dpi / 72)
                image = bitmap.to_pil().convert("L")
                png_path = scratch / f"page{i:03d}.png"
                image.save(png_path)
                ocr_lines.extend(provider.ocr_image(png_path))
        finally:
            doc.close()

    for i, printed in enumerate(printed_rows):
        if i >= len(ocr_lines):
            continue
        samples.extend(
            _collect_char_samples_from_ocr_lines(
                printed, ocr_lines[i].text, ocr_lines[i].char_conf
            )
        )
    return samples, font_label


def calibration_table(samples: list) -> list:
    """Return per-threshold ``{t, precision, recall, n_flagged, n_wrong}``.

    ``samples`` is a list of ``(wrong: bool, conf: float | None)``; entries
    with ``conf is None`` (Tesseract could not score that character at all)
    are excluded -- there is no threshold decision to make without a score.
    """
    scored = [(wrong, conf) for wrong, conf in samples if conf is not None]
    total_wrong = sum(1 for wrong, _ in scored if wrong)
    rows = []
    for t in _THRESHOLDS:
        flagged = [(wrong, conf) for wrong, conf in scored if conf < t]
        n_flagged = len(flagged)
        n_wrong_flagged = sum(1 for wrong, _ in flagged if wrong)
        precision = (n_wrong_flagged / n_flagged) if n_flagged else None
        recall = (n_wrong_flagged / total_wrong) if total_wrong else None
        rows.append(
            {
                "threshold": t,
                "precision": precision,
                "recall": recall,
                "n_flagged": n_flagged,
                "n_wrong_flagged": n_wrong_flagged,
            }
        )
    return rows, len(scored), total_wrong


def recommend_threshold(rows: list) -> "float | None":
    """Smallest threshold reaching >= 90% recall (fewer erasure-hint bytes
    flagged for the same catch rate); ``None`` if no measured threshold
    reaches it (report the best-recall one instead -- never silently pick
    a threshold that misses the plan's own bar)."""
    candidates = [
        r["threshold"] for r in rows
        if r["recall"] is not None and r["recall"] >= _RECALL_TARGET
    ]
    return min(candidates) if candidates else None


def print_table(rows: list, n_scored: int, n_wrong: int) -> None:
    print(f"scored characters: {n_scored}  (wrong: {n_wrong})")
    print(f"{'t':>5} {'precision':>10} {'recall':>8} {'flagged':>8} {'wrong_flagged':>13}")
    print("-" * 50)
    for r in rows:
        prec = f"{r['precision']:.3f}" if r["precision"] is not None else "n/a"
        rec = f"{r['recall']:.3f}" if r["recall"] is not None else "n/a"
        print(
            f"{r['threshold']:>5.2f} {prec:>10} {rec:>8} "
            f"{r['n_flagged']:>8} {r['n_wrong_flagged']:>13}"
        )
    recommended = recommend_threshold(rows)
    if recommended is not None:
        print(
            f"\nrecommended conf_threshold: {recommended} "
            f"(smallest measured threshold with >= {_RECALL_TARGET:.0%} recall of wrong chars)"
        )
    else:
        best = max(
            (r for r in rows if r["recall"] is not None),
            key=lambda r: r["recall"],
            default=None,
        )
        if best is not None:
            print(
                f"\nWARNING: no measured threshold reached {_RECALL_TARGET:.0%} recall; "
                f"best measured was t={best['threshold']} at recall={best['recall']:.3f}. "
                "Keeping the shipped default (0.6) -- do not raise it on this evidence."
            )
        else:
            print("\nno scored samples; cannot recommend a threshold from this run.")


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus", default=None,
        help="directory of real scanned images with sibling <image>.txt ground truth"
    )
    parser.add_argument("--font", default="courier", help="PDF core font or bundled font name")
    parser.add_argument("--size", type=float, default=8.0, help="font size in points")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--rows", type=int, default=120, help="synthetic-mode: random rows to render")
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--json", default=None, help="path to write the full calibration report")
    args = parser.parse_args(argv)

    if not _tesseract_available():
        print(
            "conf_calibration: Tesseract is not available on this machine "
            "(pytesseract and/or the tesseract binary is missing) -- degrading "
            "cleanly, no calibration run, no numbers fabricated. The shipped "
            "default conf_threshold stays 0.6 (see glyphive.codec.engine."
            "DEFAULT_CONF_THRESHOLD) until this is run somewhere Tesseract is "
            "installed.",
            file=sys.stderr,
        )
        return 0

    provenance = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pytesseract_version": _engine_version(),
        "source": None,
    }

    if args.corpus:
        corpus_dir = Path(args.corpus)
        if not corpus_dir.is_dir():
            raise SystemExit(f"--corpus {args.corpus!r} is not a directory")
        samples = _samples_from_corpus(corpus_dir)
        provenance["source"] = {"kind": "corpus", "path": str(corpus_dir)}
    else:
        try:
            samples, font_label = _samples_from_synthetic_render(
                args.font, args.size, args.dpi, args.rows, args.seed
            )
        except ImportError as exc:
            print(
                f"conf_calibration: synthetic-render mode needs fpdf2/pypdfium2 "
                f"({exc}); degrading cleanly, no calibration run.",
                file=sys.stderr,
            )
            return 0
        provenance["source"] = {
            "kind": "synthetic",
            "font": font_label,
            "size": args.size,
            "dpi": args.dpi,
            "rows": args.rows,
            "seed": args.seed,
        }

    if not samples:
        print(
            "conf_calibration: no aligned character samples were produced "
            "(every line either failed to render/OCR or had a length "
            "mismatch) -- no calibration numbers to report.",
            file=sys.stderr,
        )
        return 0

    rows, n_scored, n_wrong = calibration_table(samples)
    print_table(rows, n_scored, n_wrong)

    if args.json:
        report = {"provenance": provenance, "n_scored": n_scored, "n_wrong": n_wrong, "table": rows}
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote calibration report to {args.json}")

    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    raise SystemExit(main())
