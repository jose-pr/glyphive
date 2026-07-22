"""Building the starter artifacts a fine-tune needs before it can begin.

Two files must exist before ``lstmtraining`` will run:

``base.lstm``
    the recognizer extracted from a base ``.traineddata`` with
    ``combine_tessdata -e``.

``starter.traineddata``
    a traineddata carrying the NARROWED unicharset, assembled by
    ``combine_lang_model``.

Neither is conceptually hard, but ``combine_lang_model`` fails in ways that
waste an afternoon if its inputs are not exactly right, and every one of these
was hit for real on 2026-07-21:

- it needs ``<script_dir>/Latin.unicharset`` and ``Common.unicharset`` for a
  Latin script, plus ``radical-stroke.txt``; without the last one it fails with
  a bare ``Error writing unicharset!!`` that names nothing;
- it writes into ``<output_dir>/<lang>/``, which must already exist;
- it rejects a Latin unicharset whose case pairs are incomplete, which is why
  :func:`glyphive.training.build_unicharset` adds lowercase twins.

Script data is NOT bundled and is NOT downloaded implicitly: a training command
that silently reaches out to the network is a surprise. The caller passes
``--langdata DIR`` (or sets the fetch flag explicitly), and a missing directory
produces an error that says precisely which files to obtain and from where.
"""

from __future__ import annotations

import typing as _ty

from pathlib_next import Path

from .pipeline import StageError, run_stage

__all__ = [
    "LANGDATA_FILES",
    "LANGDATA_URL",
    "build_starter_traineddata",
    "extract_base_lstm",
    "find_base_traineddata",
    "check_langdata",
]

#: Files ``combine_lang_model`` needs in its ``--script_dir`` for Latin script.
LANGDATA_FILES: "tuple[str, ...]" = (
    "Latin.unicharset",
    "Common.unicharset",
    "radical-stroke.txt",
)

LANGDATA_URL = "https://github.com/tesseract-ocr/langdata"

#: Where a stock ``eng.traineddata`` usually lives. ``tessdata_best`` is
#: preferred when present: fine-tuning from the "best" (float) model is the
#: documented path, and the fast integer models are not meant for it.
_BASE_MODEL_CANDIDATES: "tuple[str, ...]" = (
    "/usr/local/share/tessdata/tessdata_best/eng.traineddata",
    "/usr/local/share/tessdata/eng.traineddata",
    "/usr/share/tesseract/tessdata/eng.traineddata",
    "/usr/share/tesseract-ocr/5/tessdata/eng.traineddata",
    "/usr/share/tessdata/eng.traineddata",
)


def find_base_traineddata(
    explicit: "_ty.Optional[_ty.Union[str, Path]]" = None,
    *,
    candidates: "_ty.Sequence[str]" = _BASE_MODEL_CANDIDATES,
    env: "_ty.Optional[_ty.Mapping[str, str]]" = None,
) -> "Path":
    """Locate the base model to fine-tune from.

    Order: an explicit path, then ``$TESSDATA_PREFIX/eng.traineddata``, then the
    usual system locations. Raises naming everything that was tried, because
    "file not found" with no list is a bad way to start a long run.
    """
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise StageError(f"base model not found at {path}")
        return path

    tried: "list[str]" = []
    if env is None:
        import os as _os

        env = _os.environ
    prefix = env.get("TESSDATA_PREFIX")
    if prefix:
        candidate = Path(prefix) / "eng.traineddata"
        tried.append(str(candidate))
        if candidate.is_file():
            return candidate
    for entry in candidates:
        tried.append(entry)
        path = Path(entry)
        if path.is_file():
            return path
    raise StageError(
        "could not find a base eng.traineddata to fine-tune from; tried: "
        + ", ".join(tried)
        + ". Pass --base-model PATH, or install the tesseract language data "
        "(tessdata_best is preferred for fine-tuning)."
    )


def check_langdata(langdata_dir: "_ty.Union[str, Path]") -> "Path":
    """Verify the script-data directory has what ``combine_lang_model`` needs.

    Named up front rather than discovered mid-run: the failure mode otherwise
    is an unexplained ``Error writing unicharset!!``.
    """
    directory = Path(langdata_dir)
    missing = [name for name in LANGDATA_FILES if not (directory / name).is_file()]
    if missing:
        raise StageError(
            f"langdata directory {directory} is missing: {', '.join(missing)}. "
            f"Download them from {LANGDATA_URL} (they are single files at the "
            "repository root) and pass --langdata pointing at the directory "
            "holding them."
        )
    return directory


def extract_base_lstm(
    base_traineddata: "Path",
    output: "Path",
    *,
    env: "_ty.Optional[dict[str, str]]" = None,
    runner: "_ty.Optional[_ty.Callable[..., _ty.Any]]" = None,
) -> "Path":
    """``combine_tessdata -e`` the recognizer out of a base model."""
    output.parent.mkdir(parents=True, exist_ok=True)
    run_stage(
        "combine_tessdata(-e)",
        ["combine_tessdata", "-e", str(base_traineddata), str(output)],
        env=env,
        runner=runner,
    )
    if not output.is_file():
        raise StageError(
            f"combine_tessdata reported success but {output} was not written"
        )
    return output


def build_starter_traineddata(
    unicharset_chars: str,
    *,
    work_dir: "Path",
    langdata_dir: "Path",
    lang: str = "glyphive",
    env: "_ty.Optional[dict[str, str]]" = None,
    runner: "_ty.Optional[_ty.Callable[..., _ty.Any]]" = None,
) -> "Path":
    """Assemble a traineddata whose unicharset is exactly our character set.

    The unicharset is extracted from a one-line corpus containing every legal
    character, so it can never be wider than the codec allows -- inheriting the
    base model's ~112-character unicharset is what lets a fine-tuned model emit
    arbitrary English text.
    """
    check_langdata(langdata_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    corpus = work_dir / "unicharset_corpus.txt"
    corpus.write_text(unicharset_chars + "\n", encoding="utf-8")

    unicharset = work_dir / f"{lang}.unicharset"
    run_stage(
        "unicharset_extractor",
        [
            "unicharset_extractor",
            "--output_unicharset", str(unicharset),
            "--norm_mode", "2",
            str(corpus),
        ],
        env=env,
        runner=runner,
    )
    if not unicharset.is_file():
        raise StageError(f"unicharset_extractor did not write {unicharset}")

    output_dir = work_dir / "starter"
    # combine_lang_model writes into <output_dir>/<lang>/ and does NOT create
    # that directory itself.
    (output_dir / lang).mkdir(parents=True, exist_ok=True)
    run_stage(
        "combine_lang_model",
        [
            "combine_lang_model",
            "--input_unicharset", str(unicharset),
            "--script_dir", str(langdata_dir),
            "--output_dir", str(output_dir),
            "--lang", lang,
        ],
        env=env,
        runner=runner,
    )
    produced = output_dir / lang / f"{lang}.traineddata"
    if not produced.is_file():
        raise StageError(
            "combine_lang_model did not produce "
            f"{produced}. Its errors are often terse -- check that the "
            "langdata directory has Latin.unicharset, Common.unicharset and "
            "radical-stroke.txt, and that the unicharset has complete case "
            "pairs."
        )
    return produced
