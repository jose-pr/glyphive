"""Tests for ``tools/conf_calibration.py`` -- the plan-3 threshold calibration tool.

This machine has no Tesseract binary, so the meaningful thing to verify here
is that the tool DEGRADES CLEANLY (never fabricates numbers, never crashes,
exits 0) and that its pure calculation helpers (calibration table math) are
correct in isolation. A real calibration run against Tesseract is exercised
manually wherever the binary is installed -- see the module docstring.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from pathlib_next import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


def _load_conf_calibration():
    spec = importlib.util.spec_from_file_location(
        "conf_calibration", str(_TOOLS_DIR / "conf_calibration.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["conf_calibration"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def conf_calibration():
    return _load_conf_calibration()


def test_degrades_cleanly_when_tesseract_unavailable(conf_calibration, monkeypatch, capsys):
    monkeypatch.setattr(conf_calibration, "_tesseract_available", lambda: False)
    rc = conf_calibration.main([])
    assert rc == 0
    err = capsys.readouterr().err
    assert "not available" in err
    assert "0.6" in err  # names the unchanged shipped default


def test_calibration_table_precision_and_recall(conf_calibration):
    # 4 scored chars: two wrong (conf 0.1, 0.55), two right (conf 0.3, 0.9).
    samples = [
        (True, 0.1),
        (True, 0.55),
        (False, 0.3),
        (False, 0.9),
    ]
    rows, n_scored, n_wrong = conf_calibration.calibration_table(samples)
    assert n_scored == 4
    assert n_wrong == 2

    by_t = {r["threshold"]: r for r in rows}
    # t=0.5: confs < 0.5 are {0.1 (wrong), 0.3 (correct)} -> flagged=2, wrong=1.
    assert by_t[0.5]["n_flagged"] == 2
    assert by_t[0.5]["n_wrong_flagged"] == 1
    assert by_t[0.5]["precision"] == pytest.approx(0.5)
    assert by_t[0.5]["recall"] == pytest.approx(0.5)  # caught 1 of 2 wrong chars

    # t=0.6: confs < 0.6 are {0.1, 0.55 (both wrong), 0.3 (correct)}.
    assert by_t[0.6]["n_flagged"] == 3  # 0.1, 0.55, 0.3
    assert by_t[0.6]["n_wrong_flagged"] == 2
    assert by_t[0.6]["recall"] == pytest.approx(1.0)  # both wrong chars caught
    assert by_t[0.6]["precision"] == pytest.approx(2 / 3)


def test_calibration_table_ignores_unscored_characters(conf_calibration):
    samples = [(True, None), (True, 0.2), (False, None)]
    rows, n_scored, n_wrong = conf_calibration.calibration_table(samples)
    assert n_scored == 1  # only the (True, 0.2) entry has a real score
    assert n_wrong == 1


def test_recommend_threshold_picks_smallest_meeting_recall_target(conf_calibration):
    rows = [
        {"threshold": 0.5, "recall": 0.5, "precision": 1.0, "n_flagged": 1, "n_wrong_flagged": 1},
        {"threshold": 0.6, "recall": 0.95, "precision": 0.8, "n_flagged": 5, "n_wrong_flagged": 4},
        {"threshold": 0.7, "recall": 1.0, "precision": 0.6, "n_flagged": 10, "n_wrong_flagged": 6},
    ]
    assert conf_calibration.recommend_threshold(rows) == 0.6


def test_recommend_threshold_none_when_no_threshold_meets_target(conf_calibration):
    rows = [
        {"threshold": 0.5, "recall": 0.2, "precision": 1.0, "n_flagged": 1, "n_wrong_flagged": 1},
    ]
    assert conf_calibration.recommend_threshold(rows) is None


def test_collect_char_samples_aligns_positionally_and_skips_length_mismatch(conf_calibration):
    samples = conf_calibration._collect_char_samples_from_ocr_lines(
        "ABCD", "ABXD", [0.9, 0.8, 0.2, 0.95]
    )
    assert samples == [(False, 0.9), (False, 0.8), (True, 0.2), (False, 0.95)]

    # Length mismatch (after stripping OCR-inserted spaces) -> no samples.
    assert conf_calibration._collect_char_samples_from_ocr_lines("ABCD", "AB", [1.0, 1.0]) == []

    # Interior OCR space is stripped before comparison.
    samples2 = conf_calibration._collect_char_samples_from_ocr_lines(
        "ABCD", "AB CD", [1.0, 1.0, 1.0, 1.0, 1.0]
    )
    assert samples2 == [(False, 1.0), (False, 1.0), (False, 1.0), (False, 1.0)]
