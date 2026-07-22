"""The end-to-end training pipeline: generate -> gate -> narrow -> fine-tune.

Every stage here is a hard gate. That is the whole point of the module: the
models this project published before it existed were not defeated by
hyperparameters, they were defeated by silently corrupt inputs. Three separate
data bugs (a display-only banner row shifting every crop, a geometric
rows-per-page estimate that over-counted a page's real rows, and box files
labelled with what stock OCR *read* rather than with the ground truth) each
independently taught a model to emit characters that were not on the page, and
none of them was visible until a byte-restore gate failed hours later.

**No model produced by this pipeline should be shipped on the strength of its
character error rate.** As of 2026-07-21 no trained model has beaten stock
Tesseract on any glyphive codec; several scored ~0% CER and restored nothing.
The acceptance gate is a byte-identical restore, and
:func:`glyphive.training.pipeline.run` reports CER only as a labelled proxy.
"""

from __future__ import annotations

import json as _json
import subprocess as _subprocess
import typing as _ty

from pathlib_next import Path

from .data import GroundTruthRow, build_training_rows, verify_pairs
from .model import TrainingError, TrainingPlan

__all__ = ["PipelineResult", "StageError", "run_stage", "write_box_file"]


class StageError(TrainingError):
    """A pipeline stage refused to continue. Always names what failed and why."""


class PipelineResult(_ty.NamedTuple):
    """What a run produced, including the numbers a caller must not over-read."""

    model_path: "_ty.Optional[Path]"
    rows_trained: int
    rows_held_out: int
    encode_failures: int
    skip_ratio: float
    cer_proxy: "_ty.Optional[float]"
    gate_verdict: str

    def describe(self) -> "dict[str, _ty.Any]":
        return {
            "model_path": str(self.model_path) if self.model_path else None,
            "rows_trained": self.rows_trained,
            "rows_held_out": self.rows_held_out,
            "encode_failures": self.encode_failures,
            "skip_ratio": self.skip_ratio,
            "cer_proxy_percent": self.cer_proxy,
            "cer_is_a_proxy": (
                "Character error rate does NOT predict restore: models have "
                "scored ~0% CER and failed to restore a single document. The "
                "acceptance gate is a byte-identical restore."
            ),
            "gate_verdict": self.gate_verdict,
        }


def write_box_file(
    box_path: "Path", text: str, width: int, height: int
) -> None:
    """Write a line-level box file carrying the AUTHORITATIVE transcription.

    Tesseract's ``lstmbox`` config generates a box file by running stock OCR
    and labelling the boxes with what it read -- which trains the new model to
    reproduce stock's mistakes and injects characters outside the codec
    alphabet into the training transcription. For LSTM training only the
    line-level text matters, so emit one ``WordStr`` box spanning the row with
    the text that was actually printed on it.
    """
    box_path.write_text(
        f"WordStr 0 0 {width} {height} 0 #{text}\n\t 0 0 {width} {height} 0\n",
        encoding="utf-8",
    )


def _parse_skip_ratio(log_text: str) -> float:
    """Pull the trainer's reported skip ratio out of its log.

    A non-zero skip ratio means ``lstmtraining`` silently discarded training
    rows it could not encode and trained on what was left; a 35% skip once went
    unnoticed for an entire run.
    """
    import re

    matches = re.findall(r"skip ratio=([0-9.]+)%", log_text)
    return float(matches[-1]) if matches else 0.0


def _count_encode_failures(log_text: str) -> int:
    return log_text.count("Can't encode transcription")


def run_stage(
    name: str,
    command: "_ty.Sequence[str]",
    *,
    env: "_ty.Optional[dict[str, str]]" = None,
    runner: "_ty.Optional[_ty.Callable[..., _ty.Any]]" = None,
) -> str:
    """Run one external stage, raising :class:`StageError` with real context.

    Swallowing a stage's stderr is how a multi-hour run ends in an unexplained
    empty output directory.
    """
    run = runner or _subprocess.run
    completed = run(list(command), env=env, capture_output=True, text=True)
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        detail = tail[-1][:400] if tail else "(no output)"
        raise StageError(f"{name} failed (exit {completed.returncode}): {detail}")
    return (completed.stdout or "") + (completed.stderr or "")


def check_training_data(
    rows: "_ty.Sequence[GroundTruthRow]",
    read_image: "_ty.Callable[[Path], str]",
    *,
    minimum_rows: int = 100,
) -> None:
    """Refuse to train on data that cannot be trusted.

    Raises rather than warns: a warning in a log is exactly how the three 2026
    data bugs survived long enough to waste two training runs.
    """
    if len(rows) < minimum_rows:
        raise StageError(
            f"only {len(rows)} training rows were generated (need at least "
            f"{minimum_rows}); check that document generation actually produced "
            "pages"
        )
    result = verify_pairs(rows, read_image)
    if not result.ok:
        raise StageError(
            result.describe()
            + " -- refusing to train on mispaired data. A row image showing a "
            "different line than its ground truth teaches the model to emit "
            "characters that are not on the page."
        )


def check_encoding(log_text: str) -> "tuple[int, float]":
    """Abort when the trainer could not encode some transcriptions.

    Returns ``(failures, skip_ratio)`` when clean, so a caller can record them.
    """
    failures = _count_encode_failures(log_text)
    skip_ratio = _parse_skip_ratio(log_text)
    if failures:
        raise StageError(
            f"{failures} transcription(s) could not be encoded with this "
            "unicharset, and the trainer would silently skip them. This means "
            "the ground truth contains characters outside the codec alphabet -- "
            "usually OCR-derived labels leaking into the training text."
        )
    if skip_ratio > 0:
        raise StageError(
            f"trainer reported a {skip_ratio:.3f}% skip ratio; training would "
            "proceed on partial data. Every row must be usable."
        )
    return failures, skip_ratio


def summarize(
    plan: TrainingPlan,
    result: PipelineResult,
) -> "dict[str, _ty.Any]":
    """The sidecar written next to a produced model. Provenance, not marketing."""
    summary = dict(plan.describe())
    summary.update(result.describe())
    summary["warning"] = (
        "Experimental. No trained model has beaten stock Tesseract on any "
        "glyphive codec as of 2026-07-21; the default codec restores unaided. "
        "Gate any model on byte-restore before relying on it."
    )
    return summary


def write_sidecar(plan: TrainingPlan, result: PipelineResult) -> "Path":
    """Persist the provenance sidecar beside the model artifact."""
    plan.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    plan.sidecar_path.write_text(
        _json.dumps(summarize(plan, result), indent=2), encoding="utf-8"
    )
    return plan.sidecar_path
