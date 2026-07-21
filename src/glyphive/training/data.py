"""Ground-truth generation and the pair-integrity gate.

The gate is the point of this module. Two independent off-by-one bugs shipped
mispaired training data on 2026-07-21 -- a display-only banner row shifting
every crop by one, and a geometric row-count estimate that claimed 78 rows on
a page that printed 58, so the tail crops fell past the last line. Both taught
a model to emit characters that were not on the page, and both were invisible
until a byte-restore gate failed hours later. :func:`verify_pairs` catches
either in seconds.
"""

from __future__ import annotations

import typing as _ty

from pathlib_next import Path

__all__ = [
    "GroundTruthRow",
    "PairCheckResult",
    "build_training_rows",
    "page_row_texts",
    "verify_pairs",
]


class GroundTruthRow(_ty.NamedTuple):
    """One training row: a row image and the text that was printed on it."""

    image: "Path"
    text: str
    page: int
    row: int


class PairCheckResult(_ty.NamedTuple):
    """Outcome of the pairing gate."""

    sampled: int
    mismatched: "list[tuple[str, str, str]]"

    @property
    def ok(self) -> bool:
        return not self.mismatched

    def describe(self) -> str:
        if self.ok:
            return f"pair check OK ({self.sampled} rows sampled)"
        head = "; ".join(
            f"{name}: printed {gt!r} but image reads {read!r}"
            for name, gt, read in self.mismatched[:3]
        )
        return (
            f"pair check FAILED: {len(self.mismatched)}/{self.sampled} sampled rows "
            f"are paired with the wrong text. {head}"
        )


def page_row_texts(
    page_texts: "_ty.Sequence[str]", *, kinds: str = "HLPQT"
) -> "list[str]":
    """The printed rows of one page that are real frames, in order.

    Display-only rows -- the ``#!glyphive`` banner, blank lines -- are dropped,
    because restore ignores them and training on them drags lowercase prose
    into the character set. Callers MUST slice row images with the same
    exclusion, or every pair after the dropped row is off by one.
    """
    return [t for t in page_texts if t.strip() and t.strip()[:1] in kinds]


def build_training_rows(
    pages: "_ty.Sequence[_ty.Sequence[str]]",
    slice_page: "_ty.Callable[[int, int], _ty.Sequence[Path]]",
    *,
    kinds: str = "HLPQT",
) -> "list[GroundTruthRow]":
    """Pair each page's frame rows with its row images.

    ``slice_page(page_index, n_rows)`` returns exactly ``n_rows`` row images
    for that page, already accounting for any leading display-only row. The
    row count comes from what the page ACTUALLY printed -- never from a
    geometric ``(height - margin) // leading`` estimate, which over-counts and
    walks the pairing off the end of the page.
    """
    rows: "list[GroundTruthRow]" = []
    for page_index, page_texts in enumerate(pages):
        texts = page_row_texts(page_texts, kinds=kinds)
        if not texts:
            continue
        images = list(slice_page(page_index, len(texts)))
        if len(images) != len(texts):
            raise ValueError(
                f"page {page_index}: got {len(images)} row images for "
                f"{len(texts)} printed frame rows -- the slicer and the "
                "ground truth disagree, which mispairs every later row"
            )
        for row_index, (image, text) in enumerate(zip(images, texts)):
            rows.append(GroundTruthRow(image, text, page_index, row_index))
    return rows


def verify_pairs(
    rows: "_ty.Sequence[GroundTruthRow]",
    read_image: "_ty.Callable[[Path], str]",
    *,
    sample: int = 60,
    prefix: int = 12,
    tolerance: float = 0.05,
    rng: "_ty.Optional[_ty.Any]" = None,
) -> PairCheckResult:
    """Sample rows and confirm each image really shows its paired text.

    Compares a normalized prefix rather than the whole line: OCR noise in the
    middle of a row is expected and harmless (CRC/RS absorb it), whereas a
    wrong *prefix* means the crop belongs to a different line entirely, which
    is the failure this gate exists to catch.

    ``tolerance`` allows a small fraction of sampled rows to disagree before
    the result is considered failed -- a badly degraded row can misread its own
    prefix. Set it to 0 to demand exact agreement.
    """
    if not rows:
        return PairCheckResult(0, [])
    if rng is None:
        import random as _random

        rng = _random.Random(0xC0DEC)
    population = list(rows)
    picks = population if len(population) <= sample else rng.sample(population, sample)
    mismatched: "list[tuple[str, str, str]]" = []
    for row in picks:
        printed = "".join(row.text.split())
        read = "".join(read_image(row.image).split())
        if printed[:prefix] != read[:prefix]:
            mismatched.append((row.image.name, printed[:40], read[:40]))
    allowed = int(len(picks) * tolerance)
    if len(mismatched) <= allowed:
        return PairCheckResult(len(picks), [])
    return PairCheckResult(len(picks), mismatched)
