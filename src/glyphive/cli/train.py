"""The ``glyphive train`` command (experimental)."""

from __future__ import annotations

import json as _json
import tempfile as _tempfile
import typing as _ty

from duho import LoggingArgs
from pathlib_next import Path

from ..training import (
    TrainingError,
    check_toolchain,
    plan_training,
)

__all__ = ["Train"]


class Train(LoggingArgs):
    """EXPERIMENTAL: train an OCR model for a codec/font/size.

    Nothing here is required to use glyphive: the default codec restores from
    a scan with stock Tesseract and no model at all. This command exists so
    that model experiments are *reproducible and honestly measured* -- as of
    2026-07-21 no trained model has beaten the stock engine on any codec, and
    several scored ~0% character error rate while failing to restore a single
    document. Accordingly this command reports CER only as a labelled proxy
    and treats a byte-identical restore as the only acceptance gate.

    The pipeline refuses to continue on corrupt inputs rather than warning:
    mispaired row images, unencodable transcriptions, or a non-zero trainer
    skip ratio all abort. Each of those corresponds to a real bug that
    silently produced a broken model before these gates existed.
    """

    _parsername_ = "train"

    codec: str
    "Codec to train for; its registered alphabet is the authority."
    ("--codec",)

    font: str
    "Font name, as passed to `create --font`."
    ("--font",)

    font_size: float
    "Font size in points."
    ("--font-size",)

    output: str
    "Directory for <codec>-<font>-<size>.traineddata and its provenance sidecar."
    ("-o", "--output")

    engine: str = "tesseract"
    "Training backend. Only 'tesseract' is supported; 'paddle' is rejected."
    ("--engine",)

    line_width: int = 60
    "Payload characters per printed line."
    ("--line-width",)

    docs: int = 40
    "Documents to generate training rows from."
    ("--docs",)

    eval_docs: int = 9
    "Held-out documents for evaluation (never used for training)."
    ("--eval-docs",)

    iterations: int = 10000
    "Maximum fine-tuning iterations."
    ("--iterations",)

    seed: int = 1
    "Seed for document generation, so a run is reproducible."
    ("--seed",)

    base_model: "_ty.Optional[str]" = None
    "Base .traineddata to fine-tune from (default: autodetect; tessdata_best preferred)."
    ("--base-model",)

    langdata: "_ty.Optional[str]" = None
    "Directory holding Latin.unicharset, Common.unicharset and radical-stroke.txt."
    ("--langdata",)

    work_dir: "_ty.Optional[str]" = None
    "Scratch directory for generated rows (default: a temporary directory)."
    ("--work-dir",)

    dry_run: bool = False
    "Validate inputs and print the resolved plan without training anything."
    ("--dry-run",)

    def __call__(self) -> int:
        logger = self._logger_
        work_dir = self.work_dir or _tempfile.mkdtemp(prefix="glyphive-train-")
        try:
            plan = plan_training(
                codec_name=self.codec,
                font=self.font,
                font_size=self.font_size,
                line_width=self.line_width,
                engine=self.engine,
                output_dir=self.output,
                work_dir=work_dir,
                docs=self.docs,
                eval_docs=self.eval_docs,
                iterations=self.iterations,
                seed=self.seed,
            )
        except TrainingError as exc:
            logger.error("%s", exc)
            return 2

        if self.dry_run:
            print(_json.dumps(plan.describe(), indent=2))
            return 0

        try:
            check_toolchain()
        except TrainingError as exc:
            logger.error("%s", exc)
            return 2

        # Generation and fine-tuning drive external Tesseract training tools and
        # are exercised on a training host, not in the test suite; the
        # integrity gates they depend on (pairing, unicharset narrowing,
        # encode/skip checks) live in glyphive.training and are unit-tested.
        from ..training.runner import execute

        try:
            result = execute(
                plan,
                logger=logger,
                base_model=self.base_model,
                langdata_dir=self.langdata,
            )
        except TrainingError as exc:
            logger.error("training aborted: %s", exc)
            return 1

        logger.info(
            "wrote %s (CER proxy %.3f%%, gate: %s)",
            result.model_path,
            result.cer_proxy if result.cer_proxy is not None else float("nan"),
            result.gate_verdict,
        )
        print(_json.dumps(result.describe(), indent=2))
        return 0
