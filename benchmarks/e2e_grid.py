#!/usr/bin/env python3
"""Reproducible E2E OCR benchmark grid: create -> rasterize -> OCR -> extract.

Replaces an earlier VM-only harness script that carried a correctness
defect: a
configuration where ``create`` itself refuses to build the document (e.g. the
selected font/size/width/line-parity combination does not leave room for the
protected header/footer frames) was counted as a restore *failure*. That
conflates "never built, so never tested" with "built and OCR/restore failed",
and produced false conclusions like "8pt is worse than 3pt" from a 4/4 result
misreported as 4/6.

This harness fixes that by giving every grid cell one of four disjoint
outcomes:

* ``restored``     -- built, OCR'd, and byte-identical to the source.
* ``not-restored`` -- built, OCR'd, but did not restore byte-identical
  (CRC/RS/SHA-256 refused a corrupt result, or produced wrong bytes).
* ``not-built``    -- ``create`` itself refused (e.g. a geometry refusal);
  the verbatim refusal reason is recorded and the cell is EXCLUDED from every
  restore-rate denominator.
* ``error``        -- an unexpected exception outside the above (harness bug,
  missing dependency at the wrong time, etc.); also excluded from restore
  rates and reported loudly.

Usage (append ``--tesseract-only`` is implicit -- the harness only exercises
the registered OCR providers that report themselves available; it degrades
gracefully, with a clear message, when none are)::

    python benchmarks/e2e_grid.py --repeat 3 --save
    python benchmarks/e2e_grid.py --font-size 3 4 8 --line-width 60 max \\
        --codec base16g-crc16-rs --line-parity 0 2 4 --repeat 5 --save

Glyphive must be importable (editable install or ``PYTHONPATH=src``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import typing as ty
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pathlib_next import Path

import glyphive
from glyphive import cli as _cli

HARNESS_VERSION = "e2e_grid-1.0.0"

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).resolve().parent / "results"
CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "e2e_grid_corpus"

#: Sensible small grid defaults. Every axis is CLI-selectable (see
#: ``build_parser``); these are only what runs when a flag is omitted.
DEFAULT_FONT_SIZES = [4.0, 8.0]
DEFAULT_LINE_WIDTHS: "ty.List[str]" = ["60", "max"]
DEFAULT_CODECS = ["base16g-crc16-rs"]
DEFAULT_LINE_PARITIES = [2]
DEFAULT_FONTS = ["ocr-b"]
DEFAULT_REPEAT = 1

STATUS_RESTORED = "restored"
STATUS_NOT_RESTORED = "not-restored"
STATUS_NOT_BUILT = "not-built"
STATUS_ERROR = "error"
_STATUSES = (STATUS_RESTORED, STATUS_NOT_RESTORED, STATUS_NOT_BUILT, STATUS_ERROR)


# --------------------------------------------------------------------------- #
# Pinned corpus
# --------------------------------------------------------------------------- #


def corpus_files() -> "ty.List[Path]":
    """Return the pinned, checked-in fixture corpus files in stable order.

    Deliberately NOT ``docs/guides/*.md`` or any other live project content --
    those change across commits, which silently changes what a "restore rate"
    means from one run to the next. The fixture corpus is small, synthetic,
    and versioned alongside this harness.
    """
    files = sorted(CORPUS_DIR.glob("*.txt"), key=str)
    if not files:
        raise RuntimeError(
            f"no fixture corpus files found under {CORPUS_DIR}; the pinned "
            "corpus is required and must not be generated at run time"
        )
    return files


def corpus_digest(files: "ty.Sequence[Path]") -> "ty.Dict[str, ty.Any]":
    """Return a sha256-per-file plus a combined digest and total byte count."""
    per_file = {}
    combined = hashlib.sha256()
    total_bytes = 0
    for path in files:
        data = path.read_bytes()
        per_file[path.name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        combined.update(path.name.encode("utf-8"))
        combined.update(data)
        total_bytes += len(data)
    return {
        "files": per_file,
        "combined_sha256": combined.hexdigest(),
        "total_bytes": total_bytes,
        "file_count": len(files),
    }


# --------------------------------------------------------------------------- #
# Grid cell definition and outcome
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Cell:
    """One grid configuration: a font/size/width/codec/line-parity point."""

    font: str
    font_size: float
    line_width: str
    codec: str
    line_parity: int
    engine: str

    def label(self) -> str:
        return (
            f"{self.font}-{self.font_size:g}pt-w{self.line_width}-"
            f"{self.codec}-lp{self.line_parity}-{self.engine}"
        )


@dataclass
class DocResult:
    """The outcome of one (cell, document, repeat-index) trial."""

    status: str
    failed_at: "ty.Optional[str]" = None
    detail: "ty.Optional[str]" = None
    document: "ty.Optional[str]" = None

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"invalid status {self.status!r}; expected one of {_STATUSES}")


@dataclass
class CellSummary:
    """Aggregated repeat-run outcomes for one grid cell.

    ``testable`` is the count of trials that were actually built (i.e.
    ``restored`` + ``not-restored``); ``not_built`` and ``errored`` are always
    reported alongside it and are NEVER folded into a restore rate. See
    :func:`summary_line` for the honest-denominator human string.
    """

    cell: Cell
    trials: "ty.List[DocResult]" = field(default_factory=list)

    @property
    def restored(self) -> int:
        return sum(1 for t in self.trials if t.status == STATUS_RESTORED)

    @property
    def not_restored(self) -> int:
        return sum(1 for t in self.trials if t.status == STATUS_NOT_RESTORED)

    @property
    def not_built(self) -> int:
        return sum(1 for t in self.trials if t.status == STATUS_NOT_BUILT)

    @property
    def errored(self) -> int:
        return sum(1 for t in self.trials if t.status == STATUS_ERROR)

    @property
    def testable(self) -> int:
        return self.restored + self.not_restored

    @property
    def total(self) -> int:
        return len(self.trials)

    @property
    def untested(self) -> bool:
        """True when every trial in this cell was ``not-built``/``error``."""
        return self.total > 0 and self.testable == 0

    def restore_rate(self) -> "ty.Optional[float]":
        """Fraction restored among TESTABLE trials only, or None if untested."""
        if self.testable == 0:
            return None
        return self.restored / self.testable

    def summary_line(self) -> str:
        """Human summary that never hides a skipped cell behind a raw n/m.

        Honest-denominator forms:

        * fully untested:  ``UNTESTED (3/3 not built)``
        * partially built:  ``2/2 testable (1 not built)``
        * fully testable:   ``4/4 testable``
        """
        skip_bits = []
        if self.not_built:
            skip_bits.append(f"{self.not_built} not built")
        if self.errored:
            skip_bits.append(f"{self.errored} errored")
        skip_note = f" ({', '.join(skip_bits)})" if skip_bits else ""
        if self.untested:
            return f"UNTESTED{skip_note}"
        return f"{self.restored}/{self.testable} testable{skip_note}"

    def restore_counts(self) -> "ty.Dict[str, ty.Optional[float]]":
        """Mean/min/max restored-trial count is meaningless for a single number,
        so report the per-trial restored-boolean statistics used by
        ``--repeat``: mean (fraction restored among testable), and min/max of
        the per-trial binary outcome (1 restored, 0 not-restored) restricted
        to testable trials -- so a repeat sweep still shows variance rather
        than collapsing to one aggregate.
        """
        testable_trials = [
            1 if t.status == STATUS_RESTORED else 0
            for t in self.trials
            if t.status in (STATUS_RESTORED, STATUS_NOT_RESTORED)
        ]
        if not testable_trials:
            return {"mean": None, "min": None, "max": None}
        return {
            "mean": round(statistics.fmean(testable_trials), 4),
            "min": min(testable_trials),
            "max": max(testable_trials),
        }

    def to_json(self) -> "ty.Dict[str, ty.Any]":
        return {
            "font": self.cell.font,
            "font_size": self.cell.font_size,
            "line_width": self.cell.line_width,
            "codec": self.cell.codec,
            "line_parity": self.cell.line_parity,
            "engine": self.cell.engine,
            "status_counts": {
                STATUS_RESTORED: self.restored,
                STATUS_NOT_RESTORED: self.not_restored,
                STATUS_NOT_BUILT: self.not_built,
                STATUS_ERROR: self.errored,
            },
            "testable": self.testable,
            "total": self.total,
            "untested": self.untested,
            "restore_rate": self.restore_rate(),
            "restore_counts": self.restore_counts(),
            "summary": self.summary_line(),
            "trials": [
                {
                    "status": t.status,
                    "failed_at": t.failed_at,
                    "detail": t.detail,
                    "document": t.document,
                }
                for t in self.trials
            ],
        }


# --------------------------------------------------------------------------- #
# One trial: create -> rasterize -> OCR -> extract -> byte-compare
# --------------------------------------------------------------------------- #


class RestoreProvider(ty.Protocol):
    """The pluggable "run one document through the pipeline" interface.

    The default implementation (:func:`real_restore_trial`) drives the real
    glyphive CLI and a real OCR engine. Unit tests inject a fake with the same
    signature so harness LOGIC (status classification, denominators, UNTESTED
    detection, repeat aggregation) is fully covered without Tesseract.
    """

    def __call__(
        self, cell: Cell, source_path: "Path", work_dir: "Path"
    ) -> DocResult:
        ...


def real_restore_trial(cell: Cell, source_path: "Path", work_dir: "Path") -> DocResult:
    """Drive the real pipeline for one (cell, document) trial.

    create -> rasterize (via ``render_document_images``) -> OCR (registered
    provider ``cell.engine``) -> ``extract`` -> byte-for-byte comparison
    against ``source_path``.
    """
    from glyphive.restore.document_images import render_document_images

    document = source_path.name
    src_root = work_dir / "src"
    src_root.mkdir(parents=True, exist_ok=True)
    dest_file = src_root / document
    dest_file.write_bytes(source_path.read_bytes())

    pdf_path = work_dir / "doc.pdf"
    create_argv = [
        "create",
        "-f",
        str(pdf_path),
        "--format",
        "pdf",
        "--font",
        cell.font,
        "--font-size",
        str(cell.font_size),
        "--codec",
        cell.codec,
        "--line-parity",
        str(cell.line_parity),
        "--line-width",
        cell.line_width,
        "--none",
        "-C",
        str(src_root),
        ".",
    ]
    if cell.line_width not in ("auto", "max"):
        # An explicit numeric width above the OCR-measured-safe cap needs
        # --force; harmless (ignored) otherwise, per glyphive.cli.create.
        create_argv.append("--force")

    try:
        rc = _cli.run(create_argv)
        if rc != 0:
            return DocResult(
                status=STATUS_ERROR,
                failed_at="create",
                detail=f"create returned non-zero exit code {rc}",
                document=document,
            )
    except SystemExit as exc:
        # create's own geometry/refusal gate -- see glyphive.cli.create:
        # "error: selected PDF geometry fits only N payload characters" and
        # sibling refusals. This is the exact case the harness must NOT
        # count as a restore failure.
        return DocResult(
            status=STATUS_NOT_BUILT,
            failed_at="create",
            detail=str(exc.code if exc.code is not None else exc),
            document=document,
        )
    except Exception as exc:  # noqa: BLE001 - classify anything else as error
        return DocResult(
            status=STATUS_ERROR,
            failed_at="create",
            detail=f"{type(exc).__name__}: {exc}",
            document=document,
        )

    image_dir = work_dir / "pages"
    try:
        render_document_images(pdf_path, image_dir, dpi=300)
    except Exception as exc:  # noqa: BLE001
        return DocResult(
            status=STATUS_ERROR,
            failed_at="rasterize",
            detail=f"{type(exc).__name__}: {exc}",
            document=document,
        )

    out_dir = work_dir / "out"
    extract_argv = [
        "extract",
        "-f",
        str(image_dir),
        "--from-images",
        "--ocr-engine",
        cell.engine,
        "-C",
        str(out_dir),
    ]
    try:
        rc = _cli.run(extract_argv)
        if rc != 0:
            return DocResult(
                status=STATUS_NOT_RESTORED,
                failed_at="extract",
                detail=f"extract returned non-zero exit code {rc}",
                document=document,
            )
    except Exception as exc:  # noqa: BLE001
        # A real OCR/restore failure (RS budget exceeded, sha256 mismatch,
        # unparseable transcript, ...) is a NOT-RESTORED outcome, not a
        # harness error -- it means the pipeline ran and failed to recover
        # the document, which is exactly what this grid measures.
        return DocResult(
            status=STATUS_NOT_RESTORED,
            failed_at="extract",
            detail=f"{type(exc).__name__}: {exc}",
            document=document,
        )

    restored_path = out_dir / document
    if not restored_path.is_file():
        return DocResult(
            status=STATUS_NOT_RESTORED,
            failed_at="extract",
            detail=f"expected restored file {restored_path} was not written",
            document=document,
        )
    if restored_path.read_bytes() != source_path.read_bytes():
        return DocResult(
            status=STATUS_NOT_RESTORED,
            failed_at="compare",
            detail="restored bytes differ from the source document",
            document=document,
        )
    return DocResult(status=STATUS_RESTORED, document=document)


# --------------------------------------------------------------------------- #
# Grid execution
# --------------------------------------------------------------------------- #


def iter_cells(
    fonts: "ty.Sequence[str]",
    font_sizes: "ty.Sequence[float]",
    line_widths: "ty.Sequence[str]",
    codecs: "ty.Sequence[str]",
    line_parities: "ty.Sequence[int]",
    engines: "ty.Sequence[str]",
) -> "ty.Iterator[Cell]":
    for font in fonts:
        for font_size in font_sizes:
            for line_width in line_widths:
                for codec_name in codecs:
                    for line_parity in line_parities:
                        for engine in engines:
                            yield Cell(
                                font=font,
                                font_size=font_size,
                                line_width=line_width,
                                codec=codec_name,
                                line_parity=line_parity,
                                engine=engine,
                            )


def run_cell(
    cell: Cell,
    documents: "ty.Sequence[Path]",
    repeat: int,
    restore_trial: "RestoreProvider",
    temp_dir: "ty.Optional[str]" = None,
) -> CellSummary:
    """Run ``repeat`` trials of ``cell`` against the corpus documents.

    ``repeat`` documents are drawn round-robin from the corpus (so
    ``--repeat 3`` with a 3-file corpus runs each file once; a smaller corpus
    repeats files; a larger one is truncated) -- always the pinned fixture
    corpus, never anything generated at run time.
    """
    summary = CellSummary(cell=cell)
    for index in range(repeat):
        document = documents[index % len(documents)]
        with tempfile.TemporaryDirectory(
            prefix="glyphive-e2e-grid-", dir=temp_dir
        ) as raw_work_dir:
            work_dir = Path(raw_work_dir)
            result = restore_trial(cell, document, work_dir)
            summary.trials.append(result)
    return summary


def run_grid(
    cells: "ty.Sequence[Cell]",
    documents: "ty.Sequence[Path]",
    repeat: int,
    restore_trial: "RestoreProvider" = real_restore_trial,
    temp_dir: "ty.Optional[str]" = None,
    on_progress: "ty.Optional[ty.Callable[[Cell, CellSummary], None]]" = None,
) -> "ty.List[CellSummary]":
    summaries = []
    for cell in cells:
        summary = run_cell(cell, documents, repeat, restore_trial, temp_dir=temp_dir)
        summaries.append(summary)
        if on_progress is not None:
            on_progress(cell, summary)
    return summaries


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


def _git_output(*args: str) -> "ty.Optional[str]":
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def git_metadata() -> "ty.Dict[str, ty.Any]":
    commit = _git_output("rev-parse", "HEAD")
    status = _git_output("status", "--porcelain", "--untracked-files=normal")
    return {"commit": commit, "dirty": None if status is None else bool(status)}


def tesseract_version() -> "ty.Optional[str]":
    """Best-effort Tesseract version string, or None if unresolvable.

    Never raises -- this is provenance metadata, not a hard requirement; a
    missing/broken Tesseract install is reported as ``None`` here and handled
    (skipped cleanly, not a stack trace) by the engine-availability check
    before any cell actually runs.
    """
    try:
        import pytesseract

        return str(pytesseract.get_tesseract_version())
    except Exception:
        return None


def available_engines() -> "ty.List[str]":
    """Registered OCR providers that report themselves installed/usable."""
    from glyphive.restore import ocr as _ocr

    names = []
    for name in _ocr.names():
        try:
            if _ocr.get(name).is_available():
                names.append(name)
        except Exception:
            continue
    return names


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def build_result(
    name: str,
    summaries: "ty.Sequence[CellSummary]",
    *,
    repeat: int,
    corpus: "ty.Sequence[Path]",
) -> "ty.Dict[str, ty.Any]":
    return {
        "schema_version": 1,
        "harness_version": HARNESS_VERSION,
        "name": name,
        "glyphive_version": glyphive.__version__,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git": git_metadata(),
        "provenance": {
            "tesseract_version": tesseract_version(),
            "corpus": corpus_digest(corpus),
        },
        "repeat": repeat,
        "cells": [summary.to_json() for summary in summaries],
    }


def print_report(summaries: "ty.Sequence[CellSummary]") -> None:
    print("=== Glyphive E2E Grid ===")
    header = f"{'cell':45s} {'summary':30s}"
    print(header)
    print("-" * len(header))
    for summary in summaries:
        label = summary.cell.label()
        line = summary.summary_line()
        flag = "  <-- UNTESTED" if summary.untested else ""
        print(f"{label:45s} {line:30s}{flag}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--font", nargs="+", default=DEFAULT_FONTS, help="font(s) to sweep"
    )
    parser.add_argument(
        "--font-size",
        nargs="+",
        type=float,
        default=DEFAULT_FONT_SIZES,
        help="font size(s) in points to sweep",
    )
    parser.add_argument(
        "--line-width",
        nargs="+",
        default=DEFAULT_LINE_WIDTHS,
        help="line width(s): 'auto', 'max', or an explicit integer",
    )
    parser.add_argument(
        "--codec", nargs="+", default=DEFAULT_CODECS, help="codec name(s)"
    )
    parser.add_argument(
        "--line-parity",
        nargs="+",
        type=int,
        default=DEFAULT_LINE_PARITIES,
        help="per-line Reed-Solomon parity byte count(s): 0, 2, or 4",
    )
    parser.add_argument(
        "--ocr-engine",
        nargs="+",
        default=None,
        help="OCR engine(s) to sweep (default: all available registered "
        "providers, auto-detected)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=DEFAULT_REPEAT,
        help="documents per cell (default: 1)",
    )
    parser.add_argument(
        "--save", action="store_true", help="write result JSON to benchmarks/results/"
    )
    parser.add_argument(
        "--name", default=None, help="result name (default: e2e-grid-<timestamp>)"
    )
    parser.add_argument(
        "--temp-dir",
        default=None,
        help="directory for scratch work (default: system temp)",
    )
    return parser


def main(argv: "ty.Optional[ty.Sequence[str]]" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    engines = args.ocr_engine
    if engines is None:
        engines = available_engines()
        if not engines:
            print(
                "error: no registered OCR engine is available on this "
                "machine (checked: easyocr, paddle, tesseract, "
                "tesseract-glyphive). Install one (e.g. Tesseract) and "
                "ensure it is importable/on PATH, or pass --ocr-engine "
                "explicitly to force a specific provider.",
                file=sys.stderr,
            )
            return 1
    else:
        from glyphive.restore import ocr as _ocr

        unavailable = []
        for engine in engines:
            try:
                if not _ocr.get(engine).is_available():
                    unavailable.append(engine)
            except Exception:
                unavailable.append(engine)
        if unavailable:
            print(
                f"error: requested OCR engine(s) {unavailable} are not "
                "available on this machine; install the required binary/"
                "model or drop them from --ocr-engine",
                file=sys.stderr,
            )
            return 1

    documents = corpus_files()
    cells = list(
        iter_cells(
            args.font,
            args.font_size,
            args.line_width,
            args.codec,
            args.line_parity,
            engines,
        )
    )

    def on_progress(cell: Cell, summary: CellSummary) -> None:
        flag = " <-- UNTESTED" if summary.untested else ""
        print(f"{cell.label()}: {summary.summary_line()}{flag}")

    summaries = run_grid(
        cells,
        documents,
        args.repeat,
        temp_dir=args.temp_dir,
        on_progress=on_progress,
    )

    print()
    print_report(summaries)

    untested = [s for s in summaries if s.untested]
    if untested:
        print()
        print(f"WARNING: {len(untested)} configuration(s) are UNTESTED (every "
              "trial was not-built/error); see per-cell 'detail' for the "
              "verbatim refusal reason.")

    if args.save:
        name = args.name or f"e2e-grid-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        result = build_result(name, summaries, repeat=args.repeat, corpus=documents)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output = RESULTS_DIR / f"{name}.json"
        output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"saved: {output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
