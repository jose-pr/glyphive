"""Driving the external Tesseract training tools.

Separated from :mod:`glyphive.training.pipeline` so the gates stay unit-testable
without a toolchain: everything in this module shells out, everything in that
one is pure.

The recipe encoded here is the one that was actually validated end to end on a
training host (2026-07-21), including the parts that are easy to get wrong:

- ground truth is what ``create`` printed, read back from the rendered PDF's
  own text layer -- not a second, independently generated render;
- the row count for a page comes from that same text layer, never from a
  geometric ``(height - margin) // leading`` estimate (which over-counted 78
  rows on pages that printed 58 and walked the pairing off the end);
- box files carry the authoritative transcription rather than being generated
  by ``lstmbox``, which labels them with what stock OCR read;
- ``--old_traineddata`` is passed because narrowing the unicharset makes it
  differ from the base model's.
"""

from __future__ import annotations

import os as _os
import typing as _ty

from pathlib_next import Path

from .data import GroundTruthRow, build_training_rows
from .model import TrainingPlan
from .pipeline import (
    PipelineResult,
    StageError,
    check_encoding,
    check_training_data,
    run_stage,
    write_box_file,
    write_sidecar,
)

__all__ = ["execute"]


def _env() -> "dict[str, str]":
    """Environment the Tesseract training tools need on a typical build host."""
    env = dict(_os.environ)
    extra_lib = "/usr/local/lib:/usr/local/lib64"
    if extra_lib not in env.get("LD_LIBRARY_PATH", ""):
        env["LD_LIBRARY_PATH"] = extra_lib + (
            ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
        )
    env.setdefault("OMP_THREAD_LIMIT", "1")
    return env


def _render_document(
    plan: TrainingPlan, source: "Path", pdf: "Path", env: "dict[str, str]"
) -> None:
    import sys

    run_stage(
        "create",
        [
            sys.executable, "-m", "glyphive", "create",
            "-f", str(pdf), "-C", str(source),
            "--format", "pdf",
            "--font", plan.font,
            "--font-size", str(plan.font_size),
            "--line-width", str(plan.line_width),
            "--force",
            "--codec", plan.codec,
            "--none", ".",
        ],
        env=env,
    )


def _page_texts(pdf: "Path") -> "list[list[str]]":
    """Per-page printed lines, read from the PDF's own text layer.

    This is the ground truth: it is exactly what was rendered, so it cannot
    drift from the images the way a separately generated text render can.
    """
    try:
        import pypdfium2 as _pdfium
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise StageError(
            "reading printed rows needs pypdfium2 (install glyphive[pdf])"
        ) from exc
    document = _pdfium.PdfDocument(str(pdf))
    try:
        return [
            [
                line
                for line in document[index].get_textpage().get_text_range().splitlines()
                if line.strip()
            ]
            for index in range(len(document))
        ]
    finally:
        document.close()


def _slice_rows(
    page_image: "_ty.Any", n_rows: int, *, row_offset: int, margin: float, leading: float
) -> "list[_ty.Any]":
    """Crop ``n_rows`` row images, starting ``row_offset`` rows below the margin.

    ``row_offset`` exists because the display-only ``#!glyphive`` banner is
    printed as a physical row but excluded from the ground truth; without it
    every crop pairs with the following line's text.
    """
    width, height = page_image.size
    pad = leading * 0.15
    crops = []
    for index in range(n_rows):
        row = index + row_offset
        top = max(0, margin + row * leading - pad)
        bottom = min(height, margin + (row + 1) * leading + pad)
        crops.append(page_image.crop((0, int(top), width, int(bottom))))
    return crops


def _build_rows(
    plan: TrainingPlan,
    *,
    doc_count: int,
    seed_base: int,
    out_dir: "Path",
    env: "dict[str, str]",
) -> "list[GroundTruthRow]":
    """Generate documents and cut them into paired (row image, printed text)."""
    import random as _random

    from PIL import Image  # noqa: F401 - imported for the crop/save path

    out_dir.mkdir(parents=True, exist_ok=True)
    scale = 300 / 72.0
    margin = 36.0 * scale
    leading = plan.font_size * 1.2 * scale
    rows: "list[GroundTruthRow]" = []

    for offset in range(doc_count):
        seed = seed_base + offset
        rng = _random.Random(seed)
        work = out_dir / f"doc{seed}"
        source = work / "src"
        source.mkdir(parents=True, exist_ok=True)
        (source / "f.bin").write_bytes(
            bytes(rng.randrange(256) for _ in range(rng.randint(300, 1500)))
        )
        pdf = work / "a.pdf"
        _render_document(plan, source, pdf, env)
        pages = _page_texts(pdf)

        import pypdfium2 as _pdfium

        document = _pdfium.PdfDocument(str(pdf))
        try:
            def slice_page(page_index: int, n_rows: int) -> "list[Path]":
                page = document[page_index]
                image = page.render(scale=scale).to_pil().convert("L")
                # Page 0 prints the display-only banner above the first frame
                # row; the ground truth excludes it, so crops must start one
                # row lower or every pair is off by one.
                page_lines = pages[page_index]
                has_banner = bool(page_lines) and page_lines[0].strip()[:1] not in "HLPQT"
                offset_rows = 1 if (page_index == 0 and has_banner) else 0
                crops = _slice_rows(
                    image, n_rows, row_offset=offset_rows,
                    margin=margin, leading=leading,
                )
                paths = []
                for row_index, crop in enumerate(crops):
                    path = out_dir / f"{seed}_{page_index}_{row_index:03d}.tif"
                    crop.save(path)
                    paths.append(path)
                return paths

            rows.extend(build_training_rows(pages, slice_page))
        finally:
            document.close()
    return rows


def _write_lstmf(rows: "_ty.Sequence[GroundTruthRow]", env: "dict[str, str]") -> "list[Path]":
    """Emit one ``.lstmf`` per row from its image plus authoritative text."""
    from PIL import Image

    produced: "list[Path]" = []
    for row in rows:
        base = Path(str(row.image)[: -len(row.image.suffix)])
        base.with_suffix(".gt.txt").write_text(row.text + "\n", encoding="utf-8")
        width, height = Image.open(row.image).size
        write_box_file(base.with_suffix(".box"), row.text, width, height)
        run_stage(
            "lstm.train",
            ["tesseract", str(row.image), str(base), "--psm", "6", "lstm.train"],
            env=env,
        )
        lstmf = base.with_suffix(".lstmf")
        if lstmf.is_file():
            produced.append(lstmf)
    return produced


def _write_list(paths: "_ty.Sequence[Path]", target: "Path") -> "Path":
    target.write_text("\n".join(str(p) for p in paths) + "\n", encoding="utf-8")
    return target


def execute(plan: TrainingPlan, *, logger: "_ty.Any" = None) -> PipelineResult:
    """Run the full pipeline for ``plan`` and return what it produced.

    Raises :class:`StageError` -- never a partial success -- when any integrity
    gate fails.
    """
    env = _env()
    log = logger.info if logger is not None else (lambda *a, **k: None)

    log("generating %d training documents", plan.docs)
    train_rows = _build_rows(
        plan, doc_count=plan.docs, seed_base=plan.seed,
        out_dir=plan.train_dir, env=env,
    )
    log("generating %d held-out documents", plan.eval_docs)
    eval_rows = _build_rows(
        plan, doc_count=plan.eval_docs, seed_base=plan.seed + 5000,
        out_dir=plan.eval_dir, env=env,
    )

    # GATE 1: every row image must actually show its paired text.
    def read_image(path: "Path") -> str:
        output = run_stage(
            "tesseract", ["tesseract", str(path), "stdout", "--psm", "7"], env=env
        )
        lines = [line for line in output.splitlines() if line.strip()]
        return lines[0] if lines else ""

    log("verifying row pairing")
    check_training_data(train_rows, read_image)

    log("building .lstmf records")
    train_files = _write_lstmf(train_rows, env)
    eval_files = _write_lstmf(eval_rows, env)
    train_list = _write_list(train_files, plan.train_dir / "list.txt")
    eval_list = _write_list(eval_files, plan.eval_dir / "list.txt")

    # GATE 2: the trainer must be able to encode every transcription.
    log("checking that every transcription encodes")
    probe = run_stage(
        "lstmtraining(probe)",
        [
            "lstmtraining",
            "--continue_from", str(plan.work_dir / "base.lstm"),
            "--model_output", str(plan.work_dir / "probe"),
            "--traineddata", str(plan.work_dir / "starter.traineddata"),
            "--old_traineddata", str(plan.work_dir / "base.traineddata"),
            "--train_listfile", str(train_list),
            "--eval_listfile", str(eval_list),
            "--max_iterations", "20",
        ],
        env=env,
    )
    failures, skip_ratio = check_encoding(probe)

    log("fine-tuning for up to %d iterations", plan.iterations)
    training_log = run_stage(
        "lstmtraining",
        [
            "lstmtraining",
            "--continue_from", str(plan.work_dir / "base.lstm"),
            "--model_output", str(plan.work_dir / "model"),
            "--traineddata", str(plan.work_dir / "starter.traineddata"),
            "--old_traineddata", str(plan.work_dir / "base.traineddata"),
            "--train_listfile", str(train_list),
            "--eval_listfile", str(eval_list),
            "--perfect_sample_delay", "19",
            "--max_iterations", str(plan.iterations),
        ],
        env=env,
    )

    import re

    best = re.findall(r"BCER\s*=\s*([0-9.]+)", training_log)
    cer_proxy = float(best[-1]) if best else None

    plan.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoints = sorted(plan.work_dir.glob("model*.checkpoint"))
    if not checkpoints:
        raise StageError("fine-tuning produced no checkpoint to finalize")
    run_stage(
        "lstmtraining(--stop_training)",
        [
            "lstmtraining", "--stop_training",
            "--continue_from", str(checkpoints[-1]),
            "--traineddata", str(plan.work_dir / "starter.traineddata"),
            "--model_output", str(plan.model_path),
        ],
        env=env,
    )

    result = PipelineResult(
        model_path=plan.model_path,
        rows_trained=len(train_files),
        rows_held_out=len(eval_files),
        encode_failures=failures,
        skip_ratio=skip_ratio,
        cer_proxy=cer_proxy,
        gate_verdict=(
            "UNGATED - run a byte-restore comparison against stock before "
            "using this model; CER alone has repeatedly been wrong"
        ),
    )
    write_sidecar(plan, result)
    return result
