"""Tests for ``tools/ocr_font_report.py`` -- the per-char/per-glyph OCR reliability tool.

Focused on the aligned-diff tally logic added to measure per-glyph DELETION
(drop) rate alongside the pre-existing substitution stats: the
2026-07-21 basemaxg gate showed a dropped glyph (OCR deleting a thin
character entirely, shrinking the line) desyncs the fixed-width frame parse
worse than a substitution does, so a candidate alphabet must be gated on
drop rate too, not just confusion independence.

No Tesseract/PDF dependency here: ``align_and_tally`` is a pure function
over synthetic (printed, ocr_line) string pairs, and ``measure()`` is
exercised with ``render_lines``/``rasterize_and_ocr`` monkeypatched to
synthetic OCR output, so this whole suite runs offline.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter, defaultdict

import pytest

from pathlib_next import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


def _load_ocr_font_report():
    spec = importlib.util.spec_from_file_location(
        "ocr_font_report", str(_TOOLS_DIR / "ocr_font_report.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["ocr_font_report"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def ofr():
    return _load_ocr_font_report()


def _tally(ofr, printed, ocr_line, *, substitutions=False):
    total: Counter = Counter()
    errors: Counter = Counter()
    misreads: dict = defaultdict(Counter)
    deletion_total: Counter = Counter()
    deletions: Counter = Counter()
    insertion_adjacent: Counter = Counter()
    kwargs = {}
    if substitutions:
        kwargs.update(total=total, errors=errors, misreads=misreads)
    ofr.align_and_tally(
        printed,
        ocr_line,
        deletion_total=deletion_total,
        deletions=deletions,
        insertion_adjacent=insertion_adjacent,
        **kwargs,
    )
    return {
        "total": total,
        "errors": errors,
        "misreads": misreads,
        "deletion_total": deletion_total,
        "deletions": deletions,
        "insertion_adjacent": insertion_adjacent,
    }


# --- align_and_tally: deletion detection ------------------------------------


def test_single_drop_is_one_deletion_not_a_cascade(ofr):
    # A naive positional zip would compare "ABCDE" vs "ACDE" position-by-position
    # and report 4 bogus substitutions (B->A? no -- B vs A, C vs C.. mismatched
    # indices produce garbage). The aligned diff must isolate the ONE true drop.
    printed = "ABCDE"
    ocr_line = "ACDE"  # B dropped
    r = _tally(ofr, printed, ocr_line)
    assert r["deletions"]["B"] == 1
    assert sum(r["deletions"].values()) == 1
    # every printed char contributes to the deletion denominator
    for c in printed:
        assert r["deletion_total"][c] == 1


def test_no_drop_when_lines_match(ofr):
    printed = "ABCDE"
    ocr_line = "ABCDE"
    r = _tally(ofr, printed, ocr_line)
    assert sum(r["deletions"].values()) == 0
    for c in printed:
        assert r["deletion_total"][c] == 1


def test_thin_glyph_drop_rate_across_many_lines(ofr):
    # Simulate a thin glyph (backtick) that OCR reliably drops, like basemaxg's
    # measured `` ` `` / `;` / `|` behavior -- confirm the per-glyph rate lands
    # near the true injected rate, aligned-diff style.
    deletion_total: Counter = Counter()
    deletions: Counter = Counter()
    insertion_adjacent: Counter = Counter()
    lines = [("A`B`C", "A`B`C"), ("A`B`C", "ABC"), ("A`B`C", "AB`C"), ("A`B`C", "A`BC")]
    # of the 4 lines, backtick occurrences: 8 total, dropped in lines 2 (both),
    # 3 (one of two), 4 (one of two) -> depends on alignment, just assert the
    # rate is high and nonzero (thin-glyph signal), not a specific integer that
    # depends on SequenceMatcher's arbitrary tie-breaking between two identical
    # dropped chars.
    for printed, ocr_line in lines:
        ofr.align_and_tally(
            printed,
            ocr_line,
            deletion_total=deletion_total,
            deletions=deletions,
            insertion_adjacent=insertion_adjacent,
        )
    assert deletions["`"] > 0
    rate = deletions["`"] / deletion_total["`"]
    assert rate > 0.3  # clearly elevated vs the untouched A/B/C chars
    assert deletions["A"] == 0
    assert deletions["B"] == 0
    assert deletions["C"] == 0


def test_pure_insertion_charges_left_neighbor(ofr):
    printed = "ABC"
    ocr_line = "AXBC"  # spurious X inserted after A
    r = _tally(ofr, printed, ocr_line)
    assert r["insertion_adjacent"]["A"] == 1
    assert sum(r["deletions"].values()) == 0


def test_insertion_at_start_charges_following_char(ofr):
    printed = "ABC"
    ocr_line = "XABC"  # spurious X inserted before the first char
    r = _tally(ofr, printed, ocr_line)
    assert r["insertion_adjacent"]["A"] == 1


def test_replace_span_splits_into_substitution_and_deletion(ofr):
    # "AB" printed, OCR only produced "X": one substitution (A->X) and one
    # drop (B), not two substitutions from a naive zip.
    total: Counter = Counter()
    errors: Counter = Counter()
    misreads: dict = defaultdict(Counter)
    deletion_total: Counter = Counter()
    deletions: Counter = Counter()
    insertion_adjacent: Counter = Counter()
    ofr.align_and_tally(
        "ABC",
        "XC",
        total=total,
        errors=errors,
        misreads=misreads,
        deletion_total=deletion_total,
        deletions=deletions,
        insertion_adjacent=insertion_adjacent,
    )
    assert deletions["B"] == 1
    assert errors["A"] == 1
    assert misreads["A"]["X"] == 1


def test_substitutions_not_tracked_when_total_omitted(ofr):
    # Deletion-only mode (what measure() uses for length-mismatched lines)
    # must not raise even though errors/misreads/total are None.
    r = _tally(ofr, "ABC", "AXC", substitutions=False)
    assert r["total"] == Counter()  # caller's own total Counter, untouched by align_and_tally
    assert sum(r["deletions"].values()) == 0  # same-length replace -> substitution shape only


# --- measure(): end-to-end aggregation with synthetic OCR, no Tesseract -----


def _patch_pipeline(monkeypatch, ofr, make_ocr_lines):
    """Stub out rendering/OCR so measure() runs offline.

    ``make_ocr_lines`` receives the ACTUAL printed rows (``measure()``
    generates these itself via its seeded ``rng.choice`` -- a test can't
    just hand it fixed strings) and returns the synthetic OCR transcript for
    them, so a test can express "OCR always drops the last char of row N"
    without needing to reproduce Python's Random stream by hand.
    """
    captured_printed: list[list[str]] = []

    def fake_render_lines(lines, font_arg, size, out_pdf, **kwargs):
        captured_printed.append(list(lines))
        out_pdf.write_bytes(b"")
        return font_arg

    def fake_rasterize_and_ocr(pdf_path, dpi, engine, scratch, *, alphabet, tesseract_constrained):
        printed_rows = captured_printed.pop(0)
        return make_ocr_lines(printed_rows)

    monkeypatch.setattr(ofr, "render_lines", fake_render_lines)
    monkeypatch.setattr(ofr, "rasterize_and_ocr", fake_rasterize_and_ocr)

    def fake_font_geometry(font_arg, size, alphabet, character_spacing_pt=0.0):
        return (10, 5, 6.0, font_arg)

    monkeypatch.setattr(ofr, "font_geometry", fake_font_geometry)


def test_measure_reports_deletion_rate_per_glyph(ofr, monkeypatch, tmp_path):
    from pathlib_next import Path as PPath

    alphabet = "ABCD"

    # Row 0 OCR'd perfectly; row 1 has its last printed char dropped.
    def make_ocr_lines(printed_rows):
        return [printed_rows[0], printed_rows[1][:-1]]

    _patch_pipeline(monkeypatch, ofr, make_ocr_lines)

    result = ofr.measure(
        alphabet=alphabet,
        font_arg="courier",
        engine="tesseract",
        dpi=300,
        size=8.0,
        rows=2,
        line_length_override=4,
        seed=1234,
        scratch=PPath(str(tmp_path)),
    )

    assert "per_char" in result
    for c in alphabet:
        info = result["per_char"][c]
        assert "deletion_rate" in info
        assert "deletions" in info
        assert "deletion_samples" in info
        assert "insertion_adjacent" in info
        # existing fields still present (backward compatible shape)
        assert "samples" in info
        assert "errors" in info
        assert "error_rate" in info
        assert "misreads" in info

    # Whichever glyph landed in the dropped position accumulated exactly one
    # deletion; the total deletions across the alphabet is exactly 1.
    total_deletions = sum(result["per_char"][c]["deletions"] for c in alphabet)
    assert total_deletions == 1
    assert result["drop_pairs"]  # the highlight list surfaces the drop
    dropped_char, count, rate = result["drop_pairs"][0]
    assert count == 1
    assert rate == pytest.approx(1.0)


def test_measure_backward_compatible_when_no_drops(ofr, monkeypatch, tmp_path):
    from pathlib_next import Path as PPath

    alphabet = "AB"
    captured: list[list[str]] = []

    # Row 0 OCR'd cleanly; row 1 comes back with every char transposed
    # (reversed) -- a same-length substitution-only line, no drop.
    def make_ocr_lines(printed_rows):
        captured.append(list(printed_rows))
        return [printed_rows[0], printed_rows[1][::-1]]

    _patch_pipeline(monkeypatch, ofr, make_ocr_lines)

    result = ofr.measure(
        alphabet=alphabet,
        font_arg="courier",
        engine="tesseract",
        dpi=300,
        size=8.0,
        rows=2,
        line_length_override=2,
        seed=1,
        scratch=PPath(str(tmp_path)),
    )

    # Recompute what the OLD naive positional zip would have produced (the
    # exact pre-existing algorithm), and assert measure() matches it exactly
    # -- this is the backward-compatibility guarantee, independent of
    # whatever the seeded RNG happens to print.
    printed_rows = captured[0]
    ocr_rows = [printed_rows[0], printed_rows[1][::-1]]
    expected_total: Counter = Counter()
    expected_errors: Counter = Counter()
    for printed, ocr_line in zip(printed_rows, ocr_rows):
        for pc, oc in zip(printed, ocr_line):
            expected_total[pc] += 1
            if oc != pc:
                expected_errors[pc] += 1

    for c in alphabet:
        assert result["per_char"][c]["samples"] == expected_total[c]
        assert result["per_char"][c]["errors"] == expected_errors[c]
    # NOTE: a same-length line can still register an aligned-diff deletion --
    # e.g. "BA" OCR'd as "AB" is a pure transposition, which SequenceMatcher
    # reports as insert+delete rather than 2 substitutions (see
    # test_single_drop_is_one_deletion_not_a_cascade's module docstring
    # discussion). That's an intentional, orthogonal signal from the
    # deletion-only aligned pass; it does not touch samples/errors above,
    # which is the actual backward-compatibility guarantee under test here.


def test_measure_length_mismatch_lines_now_feed_deletion_stats(ofr, monkeypatch, tmp_path):
    from pathlib_next import Path as PPath

    alphabet = "ABCDE"
    dropped_char_holder: list[str] = []

    # A single line, length 5 printed, OCR drops its middle char (index 2) ->
    # length mismatch (4 != 5). Previously this whole line was excluded from
    # ALL per-char stats; now it must still contribute to deletion stats even
    # though it stays excluded from substitution stats (verified below).
    def make_ocr_lines(printed_rows):
        printed = printed_rows[0]
        dropped_char_holder.append(printed[2])
        return [printed[:2] + printed[3:]]

    _patch_pipeline(monkeypatch, ofr, make_ocr_lines)

    result = ofr.measure(
        alphabet=alphabet,
        font_arg="courier",
        engine="tesseract",
        dpi=300,
        size=8.0,
        rows=1,
        line_length_override=5,
        seed=1,
        scratch=PPath(str(tmp_path)),
    )
    assert result["length_mismatches"] == 1
    # Old behavior preserved: a length-mismatched line contributes NOTHING to
    # substitution `total`/`errors` (so `samples` stays 0 for every glyph).
    for c in alphabet:
        assert result["per_char"][c]["samples"] == 0
    # New behavior: the drop is still captured via the aligned diff. (Don't
    # assume the dropped char is unique in the line -- assert on its actual
    # occurrence count rather than hardcoding rate == 1.0.)
    dropped = dropped_char_holder[0]
    info = result["per_char"][dropped]
    assert info["deletions"] == 1
    assert info["deletion_samples"] >= 1
    assert info["deletion_rate"] == pytest.approx(1 / info["deletion_samples"])
