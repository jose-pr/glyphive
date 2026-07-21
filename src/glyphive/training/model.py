"""Toolchain discovery, unicharset construction, and the training plan.

Kept free of subprocess side effects where possible so the interesting logic
(what would run, and whether the inputs are sane) is unit-testable without a
Tesseract installation.
"""

from __future__ import annotations

import shutil as _shutil
import typing as _ty

from pathlib_next import Path

__all__ = [
    "ToolchainError",
    "TrainingError",
    "TrainingPlan",
    "build_unicharset",
    "check_toolchain",
    "plan_training",
]

#: Executables the Tesseract training path needs, with what each is for.
REQUIRED_TOOLS: "dict[str, str]" = {
    "tesseract": "render row images into .lstmf training records",
    "lstmtraining": "fine-tune the LSTM and stop training into a .traineddata",
    "unicharset_extractor": "derive a unicharset from ground truth",
    "combine_lang_model": "assemble the starter traineddata",
    "combine_tessdata": "extract the base model's .lstm",
}


class TrainingError(RuntimeError):
    """A training precondition failed. The message always names the offender."""


class ToolchainError(TrainingError):
    """A required training executable is missing from PATH."""


def check_toolchain(
    *, which: "_ty.Callable[[str], _ty.Optional[str]]" = _shutil.which
) -> "dict[str, str]":
    """Resolve every required tool, or raise naming *all* of the missing ones.

    Reporting the full set at once matters: discovering these one failure per
    multi-hour run is how the VM-era scripts wasted afternoons.
    """
    found: "dict[str, str]" = {}
    missing: "list[str]" = []
    for tool, purpose in REQUIRED_TOOLS.items():
        path = which(tool)
        if path is None:
            missing.append(f"{tool} ({purpose})")
        else:
            found[tool] = path
    if missing:
        raise ToolchainError(
            "missing Tesseract training tools on PATH: "
            + "; ".join(missing)
            + ". Install the tesseract training tools (they are a separate "
            "package/build from the tesseract binary itself)."
        )
    return found


def build_unicharset(alphabet: str, delimiter: str, *, kinds: str = "HLPQT") -> str:
    """The exact character set a model for this codec may ever emit.

    Includes the codec alphabet, its delimiter, the frame kind letters and the
    field-separating space. Lowercase twins of every uppercase letter are added
    because ``combine_lang_model`` refuses a Latin unicharset whose case pairs
    are incomplete -- they never appear in the data, so the model does not
    learn to emit them; this is still far narrower than the ~112-character
    ``eng`` unicharset that fine-tuning would otherwise inherit.

    Display-only prose (the ``#!glyphive`` banner, the ``PAGE n/m`` footer
    suffix) is deliberately excluded: those rows are not training data, because
    restore ignores them and they drag lowercase words into the character set.
    """
    chars = set(alphabet) | set(delimiter) | set(kinds) | {" "}
    chars |= {c.lower() for c in chars if c.isupper()}
    return "".join(sorted(chars))


class TrainingPlan:
    """Everything a run will do, resolved and validated, before it does any of it.

    Exists so ``--dry-run`` can report a complete, checkable plan and so tests
    can assert on path derivation without a toolchain present.
    """

    def __init__(
        self,
        *,
        codec: str,
        alphabet: str,
        delimiter: str,
        font: str,
        font_size: float,
        line_width: int,
        engine: str,
        output_dir: "Path",
        work_dir: "Path",
        docs: int,
        eval_docs: int,
        iterations: int,
        seed: int,
    ) -> None:
        if font_size <= 0:
            raise TrainingError(f"--font-size must be positive, got {font_size}")
        if line_width < 1:
            raise TrainingError(f"--line-width must be >= 1, got {line_width}")
        if docs < 1 or eval_docs < 1:
            raise TrainingError("--docs and --eval-docs must both be >= 1")
        if iterations < 1:
            raise TrainingError(f"--iterations must be >= 1, got {iterations}")
        self.codec = codec
        self.alphabet = alphabet
        self.delimiter = delimiter
        self.font = font
        self.font_size = font_size
        self.line_width = line_width
        self.engine = engine
        self.output_dir = Path(output_dir)
        self.work_dir = Path(work_dir)
        self.docs = docs
        self.eval_docs = eval_docs
        self.iterations = iterations
        self.seed = seed
        self.unicharset = build_unicharset(alphabet, delimiter)

    @property
    def model_name(self) -> str:
        """Stable, self-describing artifact name: codec, font and size."""
        size = f"{self.font_size:g}".replace(".", "p")
        return f"{self.codec}-{self.font}-{size}"

    @property
    def model_path(self) -> "Path":
        return self.output_dir / f"{self.model_name}.traineddata"

    @property
    def sidecar_path(self) -> "Path":
        return self.output_dir / f"{self.model_name}.json"

    @property
    def train_dir(self) -> "Path":
        return self.work_dir / "train"

    @property
    def eval_dir(self) -> "Path":
        return self.work_dir / "eval"

    def describe(self) -> "dict[str, _ty.Any]":
        """A JSON-able summary -- also the basis of the artifact's sidecar."""
        return {
            "codec": self.codec,
            "alphabet": self.alphabet,
            "delimiter": self.delimiter,
            "unicharset": self.unicharset,
            "unicharset_size": len(self.unicharset),
            "font": self.font,
            "font_size": self.font_size,
            "line_width": self.line_width,
            "engine": self.engine,
            "docs": self.docs,
            "eval_docs": self.eval_docs,
            "iterations": self.iterations,
            "seed": self.seed,
            "model_path": str(self.model_path),
            "acceptance_gate": "byte-identical restore (CER is a proxy only)",
        }


def plan_training(
    *,
    codec_name: str,
    font: str,
    font_size: float,
    line_width: int,
    engine: str,
    output_dir: "_ty.Union[str, Path]",
    work_dir: "_ty.Union[str, Path]",
    docs: int = 40,
    eval_docs: int = 9,
    iterations: int = 10000,
    seed: int = 1,
    get_codec: "_ty.Optional[_ty.Callable[[str], _ty.Any]]" = None,
) -> TrainingPlan:
    """Resolve a codec through the registry and build a validated plan.

    The codec's own spec is the authority for the alphabet and delimiter -- a
    hardcoded alphabet string is how a whitelist silently drifts out of sync
    with the codec it is meant to describe.
    """
    if engine not in ("tesseract", "paddle"):
        raise TrainingError(
            f"unknown --engine {engine!r}; supported: tesseract (paddle is "
            "experimental and not viable for stock inference -- it applies "
            "language modelling and merges printed rows)"
        )
    if get_codec is None:  # pragma: no cover - trivial indirection for tests
        from .. import codec as _codec

        get_codec = _codec.get
    try:
        spec = get_codec(codec_name)._spec
    except Exception as exc:  # noqa: BLE001 - re-raised with a better message
        raise TrainingError(f"unknown codec {codec_name!r}: {exc}") from None
    return TrainingPlan(
        codec=codec_name,
        alphabet=spec.alphabet,
        delimiter=spec.delimiter,
        font=font,
        font_size=font_size,
        line_width=line_width,
        engine=engine,
        output_dir=Path(output_dir),
        work_dir=Path(work_dir),
        docs=docs,
        eval_docs=eval_docs,
        iterations=iterations,
        seed=seed,
    )
