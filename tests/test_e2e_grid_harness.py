"""Unit coverage for the E2E grid harness's outcome/aggregation LOGIC.

Deliberately runs without Tesseract (or any OCR engine): every test injects a
fake ``restore_trial`` callable matching :class:`benchmarks.e2e_grid.RestoreProvider`
instead of driving the real pipeline, so this file exercises exactly the
correctness fix from ``.agents/plans/benchmark_harness_correctness.md`` --
never counting a build refusal as a restore failure, honest denominators, and
loud UNTESTED reporting -- independent of any installed OCR binary.
"""

from __future__ import annotations

import sys
from pathlib import Path as _StdPath

import pytest

_BENCHMARKS_DIR = _StdPath(__file__).resolve().parent.parent / "benchmarks"
if str(_BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARKS_DIR))

import e2e_grid  # noqa: E402


def _cell(**overrides):
    defaults = dict(
        font="ocr-b",
        font_size=8.0,
        line_width="60",
        codec="base16g-crc16-rs",
        line_parity=2,
        engine="tesseract",
    )
    defaults.update(overrides)
    return e2e_grid.Cell(**defaults)


# --------------------------------------------------------------------------- #
# (a) a geometry-refused cell becomes not-built with the verbatim reason and
#     is excluded from the denominator
# --------------------------------------------------------------------------- #


def test_geometry_refusal_becomes_not_built_with_verbatim_reason(tmp_path):
    refusal = (
        "error: selected PDF geometry fits only 56 payload characters; at "
        "least 60 are required for protected header/footer frames"
    )

    def fake_refuses(cell, source_path, work_dir):
        return e2e_grid.DocResult(
            status=e2e_grid.STATUS_NOT_BUILT,
            failed_at="create",
            detail=refusal,
            document=source_path.name,
        )

    doc = tmp_path / "doc.txt"
    doc.write_text("hello\n")
    summary = e2e_grid.run_cell(_cell(), [doc], repeat=1, restore_trial=fake_refuses)

    assert summary.trials[0].status == e2e_grid.STATUS_NOT_BUILT
    assert summary.trials[0].detail == refusal
    # Excluded from the denominator: testable is 0, not 1.
    assert summary.testable == 0
    assert summary.not_built == 1
    assert summary.restore_rate() is None


def test_not_built_never_counts_against_restore_rate_in_a_mixed_cell(tmp_path):
    """A cell with 2 restored + 2 not-built reports 2/2 testable, not 2/4."""
    docs = [tmp_path / f"doc{i}.txt" for i in range(4)]
    for doc in docs:
        doc.write_text("hello\n")

    outcomes = iter(
        [
            e2e_grid.STATUS_RESTORED,
            e2e_grid.STATUS_NOT_BUILT,
            e2e_grid.STATUS_RESTORED,
            e2e_grid.STATUS_NOT_BUILT,
        ]
    )

    def fake(cell, source_path, work_dir):
        status = next(outcomes)
        return e2e_grid.DocResult(
            status=status,
            failed_at=None if status == e2e_grid.STATUS_RESTORED else "create",
            detail=None if status == e2e_grid.STATUS_RESTORED else "geometry refusal",
            document=source_path.name,
        )

    summary = e2e_grid.run_cell(_cell(), docs, repeat=4, restore_trial=fake)

    assert summary.testable == 2
    assert summary.restored == 2
    assert summary.not_built == 2
    assert summary.restore_rate() == 1.0
    assert summary.summary_line() == "2/2 testable (2 not built)"
    assert "2/4" not in summary.summary_line()


# --------------------------------------------------------------------------- #
# (b) an all-not-built configuration is reported UNTESTED
# --------------------------------------------------------------------------- #


def test_all_not_built_cell_is_untested(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("hello\n")

    def fake_all_refuse(cell, source_path, work_dir):
        return e2e_grid.DocResult(
            status=e2e_grid.STATUS_NOT_BUILT,
            failed_at="create",
            detail="error: selected PDF geometry fits only 40 payload characters",
            document=source_path.name,
        )

    summary = e2e_grid.run_cell(_cell(), [doc], repeat=3, restore_trial=fake_all_refuse)

    assert summary.untested is True
    assert summary.testable == 0
    assert summary.summary_line().startswith("UNTESTED")
    # Never an empty or whole-number score standing in for "untested".
    assert summary.restore_rate() is None


def test_all_errored_cell_is_also_untested(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("hello\n")

    def fake_all_error(cell, source_path, work_dir):
        return e2e_grid.DocResult(
            status=e2e_grid.STATUS_ERROR,
            failed_at="rasterize",
            detail="RuntimeError: pypdfium2 not installed",
            document=source_path.name,
        )

    summary = e2e_grid.run_cell(_cell(), [doc], repeat=2, restore_trial=fake_all_error)

    assert summary.untested is True
    assert summary.errored == 2
    assert summary.summary_line() == "UNTESTED (2 errored)"


def test_a_single_testable_trial_is_not_untested(tmp_path):
    docs = [tmp_path / "d0.txt", tmp_path / "d1.txt"]
    for doc in docs:
        doc.write_text("hello\n")

    outcomes = iter([e2e_grid.STATUS_NOT_BUILT, e2e_grid.STATUS_RESTORED])

    def fake(cell, source_path, work_dir):
        return e2e_grid.DocResult(status=next(outcomes), document=source_path.name)

    summary = e2e_grid.run_cell(_cell(), docs, repeat=2, restore_trial=fake)

    assert summary.untested is False
    assert summary.testable == 1
    assert summary.restored == 1


# --------------------------------------------------------------------------- #
# (c) the summary string shows "n/m testable (k not built)"
# --------------------------------------------------------------------------- #


def test_summary_string_format_n_of_m_testable_k_not_built(tmp_path):
    docs = [tmp_path / f"d{i}.txt" for i in range(6)]
    for doc in docs:
        doc.write_text("hello\n")

    # 4 restored, 2 not-built -- the exact 4/4-testable-(2-not-built) scenario
    # named in the plan (never reported as "4/6").
    outcomes = iter(
        [e2e_grid.STATUS_RESTORED] * 4 + [e2e_grid.STATUS_NOT_BUILT] * 2
    )

    def fake(cell, source_path, work_dir):
        return e2e_grid.DocResult(status=next(outcomes), document=source_path.name)

    summary = e2e_grid.run_cell(_cell(), docs, repeat=6, restore_trial=fake)

    assert summary.summary_line() == "4/4 testable (2 not built)"


def test_summary_string_with_no_skips_omits_the_parenthetical(tmp_path):
    docs = [tmp_path / f"d{i}.txt" for i in range(3)]
    for doc in docs:
        doc.write_text("hello\n")

    def fake(cell, source_path, work_dir):
        return e2e_grid.DocResult(status=e2e_grid.STATUS_RESTORED, document=source_path.name)

    summary = e2e_grid.run_cell(_cell(), docs, repeat=3, restore_trial=fake)

    assert summary.summary_line() == "3/3 testable"
    assert "not built" not in summary.summary_line()


def test_not_restored_counts_toward_testable_denominator_but_not_restored(tmp_path):
    docs = [tmp_path / f"d{i}.txt" for i in range(3)]
    for doc in docs:
        doc.write_text("hello\n")

    outcomes = iter(
        [e2e_grid.STATUS_RESTORED, e2e_grid.STATUS_NOT_RESTORED, e2e_grid.STATUS_RESTORED]
    )

    def fake(cell, source_path, work_dir):
        return e2e_grid.DocResult(status=next(outcomes), document=source_path.name)

    summary = e2e_grid.run_cell(_cell(), docs, repeat=3, restore_trial=fake)

    assert summary.summary_line() == "2/3 testable"
    assert summary.restore_rate() == pytest.approx(2 / 3)


# --------------------------------------------------------------------------- #
# (d) repeat aggregation math
# --------------------------------------------------------------------------- #


def test_repeat_aggregation_mean_min_max(tmp_path):
    docs = [tmp_path / f"d{i}.txt" for i in range(5)]
    for doc in docs:
        doc.write_text("hello\n")

    # 3 restored, 2 not-restored among 5 testable trials.
    outcomes = iter(
        [
            e2e_grid.STATUS_RESTORED,
            e2e_grid.STATUS_RESTORED,
            e2e_grid.STATUS_NOT_RESTORED,
            e2e_grid.STATUS_RESTORED,
            e2e_grid.STATUS_NOT_RESTORED,
        ]
    )

    def fake(cell, source_path, work_dir):
        return e2e_grid.DocResult(status=next(outcomes), document=source_path.name)

    summary = e2e_grid.run_cell(_cell(), docs, repeat=5, restore_trial=fake)
    counts = summary.restore_counts()

    assert counts["mean"] == pytest.approx(3 / 5)
    assert counts["min"] == 0
    assert counts["max"] == 1


def test_repeat_aggregation_all_restored_gives_min_max_one(tmp_path):
    docs = [tmp_path / f"d{i}.txt" for i in range(4)]
    for doc in docs:
        doc.write_text("hello\n")

    def fake(cell, source_path, work_dir):
        return e2e_grid.DocResult(status=e2e_grid.STATUS_RESTORED, document=source_path.name)

    summary = e2e_grid.run_cell(_cell(), docs, repeat=4, restore_trial=fake)
    counts = summary.restore_counts()

    assert counts == {"mean": 1.0, "min": 1, "max": 1}


def test_repeat_aggregation_returns_none_triple_when_fully_untested(tmp_path):
    doc = tmp_path / "d.txt"
    doc.write_text("hello\n")

    def fake(cell, source_path, work_dir):
        return e2e_grid.DocResult(
            status=e2e_grid.STATUS_NOT_BUILT, detail="refused", document=source_path.name
        )

    summary = e2e_grid.run_cell(_cell(), [doc], repeat=3, restore_trial=fake)
    counts = summary.restore_counts()

    assert counts == {"mean": None, "min": None, "max": None}


def test_repeat_documents_are_drawn_round_robin_from_the_pinned_corpus(tmp_path):
    docs = [tmp_path / "d0.txt", tmp_path / "d1.txt"]
    for doc in docs:
        doc.write_text("hello\n")

    seen = []

    def fake(cell, source_path, work_dir):
        seen.append(source_path.name)
        return e2e_grid.DocResult(status=e2e_grid.STATUS_RESTORED, document=source_path.name)

    summary = e2e_grid.run_cell(_cell(), docs, repeat=5, restore_trial=fake)

    assert seen == ["d0.txt", "d1.txt", "d0.txt", "d1.txt", "d0.txt"]
    assert summary.total == 5


# --------------------------------------------------------------------------- #
# DocResult validation and JSON shape
# --------------------------------------------------------------------------- #


def test_docresult_rejects_an_invalid_status():
    with pytest.raises(ValueError, match="invalid status"):
        e2e_grid.DocResult(status="bogus")


def test_cell_summary_to_json_reports_status_counts_and_untested_flag(tmp_path):
    docs = [tmp_path / f"d{i}.txt" for i in range(2)]
    for doc in docs:
        doc.write_text("hello\n")

    def fake(cell, source_path, work_dir):
        return e2e_grid.DocResult(
            status=e2e_grid.STATUS_NOT_BUILT,
            detail="error: selected PDF geometry fits only 40 payload characters",
            document=source_path.name,
        )

    summary = e2e_grid.run_cell(_cell(), docs, repeat=2, restore_trial=fake)
    payload = summary.to_json()

    assert payload["untested"] is True
    assert payload["status_counts"] == {
        "restored": 0,
        "not-restored": 0,
        "not-built": 2,
        "error": 0,
    }
    assert payload["testable"] == 0
    assert payload["total"] == 2
    assert payload["restore_rate"] is None
    assert all(
        "geometry fits only 40 payload characters" in trial["detail"]
        for trial in payload["trials"]
    )


# --------------------------------------------------------------------------- #
# Corpus pinning
# --------------------------------------------------------------------------- #


def test_pinned_corpus_is_nonempty_and_deterministic():
    files = e2e_grid.corpus_files()
    assert files, "the pinned fixture corpus must not be empty"

    digest_a = e2e_grid.corpus_digest(files)
    digest_b = e2e_grid.corpus_digest(files)
    assert digest_a == digest_b
    assert digest_a["file_count"] == len(files)
    assert digest_a["total_bytes"] > 0
    for name, info in digest_a["files"].items():
        assert len(info["sha256"]) == 64
        assert info["bytes"] > 0


def test_pinned_corpus_files_are_real_txt_fixtures_not_docs_guides():
    files = e2e_grid.corpus_files()
    for path in files:
        assert "docs" not in path.parts, "must not read from docs/guides at run time"
        assert path.suffix == ".txt"


# --------------------------------------------------------------------------- #
# Grid construction
# --------------------------------------------------------------------------- #


def test_iter_cells_produces_the_full_cartesian_product():
    cells = list(
        e2e_grid.iter_cells(
            fonts=["ocr-b"],
            font_sizes=[3.0, 4.0],
            line_widths=["60", "max"],
            codecs=["base16g-crc16-rs"],
            line_parities=[0, 2],
            engines=["tesseract"],
        )
    )
    assert len(cells) == 2 * 2 * 2
    assert all(isinstance(c, e2e_grid.Cell) for c in cells)


def test_run_grid_reports_one_summary_per_cell(tmp_path):
    docs = [tmp_path / "d0.txt"]
    docs[0].write_text("hello\n")
    cells = [
        _cell(font_size=3.0),
        _cell(font_size=4.0),
    ]

    def fake(cell, source_path, work_dir):
        return e2e_grid.DocResult(status=e2e_grid.STATUS_RESTORED, document=source_path.name)

    summaries = e2e_grid.run_grid(cells, docs, repeat=1, restore_trial=fake)

    assert len(summaries) == 2
    assert {s.cell.font_size for s in summaries} == {3.0, 4.0}


# --------------------------------------------------------------------------- #
# Graceful degradation without any OCR engine (no Tesseract required to test)
# --------------------------------------------------------------------------- #


def test_main_reports_a_clear_error_and_nonzero_exit_when_no_engine_available(
    monkeypatch, capsys
):
    monkeypatch.setattr(e2e_grid, "available_engines", lambda: [])

    rc = e2e_grid.main(["--repeat", "1"])

    assert rc != 0
    captured = capsys.readouterr()
    assert "no registered OCR engine is available" in captured.err
    assert "Traceback" not in captured.err
