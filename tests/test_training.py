"""Tests for :mod:`glyphive.training`.

These run without Tesseract: the toolchain lookup, unicharset construction,
plan/path derivation and -- most importantly -- the pairing gate are all
exercised with injected fakes. The pairing gate is the reason this module
exists, so its failure modes are tested directly (see the module docstring in
``glyphive.training`` for the two real off-by-one bugs it was written to catch).
"""

from __future__ import annotations

import pytest
from pathlib_next import Path

from glyphive.training import (
    GroundTruthRow,
    ToolchainError,
    TrainingError,
    build_training_rows,
    build_unicharset,
    check_toolchain,
    plan_training,
    verify_pairs,
)
from glyphive.training.data import page_row_texts


# --------------------------------------------------------------------------- #
# Toolchain
# --------------------------------------------------------------------------- #
def test_check_toolchain_reports_every_missing_tool_at_once():
    """One tool per failed run is how the VM scripts wasted afternoons."""
    with pytest.raises(ToolchainError) as excinfo:
        check_toolchain(which=lambda _name: None)
    message = str(excinfo.value)
    for tool in ("tesseract", "lstmtraining", "unicharset_extractor",
                 "combine_lang_model", "combine_tessdata"):
        assert tool in message


def test_check_toolchain_returns_resolved_paths():
    found = check_toolchain(which=lambda name: f"/usr/local/bin/{name}")
    assert found["lstmtraining"] == "/usr/local/bin/lstmtraining"


# --------------------------------------------------------------------------- #
# Unicharset narrowing
# --------------------------------------------------------------------------- #
def test_build_unicharset_covers_alphabet_delimiter_kinds_and_space():
    chars = build_unicharset("ABC34", "#")
    for expected in "ABC34#HLPQT ":
        assert expected in chars


def test_build_unicharset_adds_lowercase_case_pairs():
    """combine_lang_model refuses a Latin unicharset with incomplete case pairs."""
    chars = build_unicharset("AB", "#")
    assert "a" in chars and "b" in chars


def test_build_unicharset_excludes_display_only_prose():
    """The banner/footer characters must not widen the trainable set."""
    chars = build_unicharset("ABCDHKLMPRTVXY34", "#")
    # 'g', 'i', 'v' come from "#!glyphive"; only 'v' is legitimately present
    # (lowercase twin of alphabet 'V'), 'g'/'i' have no uppercase twin here.
    assert "g" not in chars
    assert "i" not in chars
    assert "/" not in chars  # the PAGE n/m footer suffix


# --------------------------------------------------------------------------- #
# Plan / path derivation
# --------------------------------------------------------------------------- #
class _FakeSpec:
    alphabet = "ABCDHKLMPRTVXY34"
    delimiter = "#"


class _FakeCodec:
    _spec = _FakeSpec()


def _plan(**overrides):
    kwargs = dict(
        codec_name="base16g-crc16-rs",
        font="courier",
        font_size=8,
        line_width=60,
        engine="tesseract",
        output_dir="/models",
        work_dir="/work",
        get_codec=lambda _name: _FakeCodec(),
    )
    kwargs.update(overrides)
    return plan_training(**kwargs)


def test_plan_derives_a_self_describing_artifact_name():
    plan = _plan()
    assert plan.model_name == "base16g-crc16-rs-courier-8"
    assert plan.model_path.name.endswith(".traineddata")
    assert plan.sidecar_path.name.endswith(".json")


def test_plan_uses_the_codec_registry_as_the_alphabet_authority():
    """A hardcoded alphabet is how a whitelist drifts out of sync."""
    plan = _plan()
    assert plan.alphabet == _FakeSpec.alphabet
    assert plan.delimiter == _FakeSpec.delimiter


def test_plan_rejects_an_unknown_engine():
    with pytest.raises(TrainingError, match="unknown --engine"):
        _plan(engine="magic-ocr")


def test_plan_rejects_an_unknown_codec():
    def _boom(_name):
        raise KeyError("nope")

    with pytest.raises(TrainingError, match="unknown codec"):
        _plan(get_codec=_boom)


@pytest.mark.parametrize(
    "bad", [{"font_size": 0}, {"line_width": 0}, {"docs": 0}, {"iterations": 0}]
)
def test_plan_validates_numeric_arguments(bad):
    with pytest.raises(TrainingError):
        _plan(**bad)


def test_plan_describe_names_byte_restore_as_the_gate():
    """CER must never be presented as the acceptance criterion."""
    described = _plan().describe()
    assert "byte-identical restore" in described["acceptance_gate"]
    assert described["unicharset_size"] < 112  # narrower than eng's unicharset


# --------------------------------------------------------------------------- #
# Ground-truth row construction
# --------------------------------------------------------------------------- #
def test_page_row_texts_drops_display_only_rows():
    rows = page_row_texts(
        ["#!glyphive v1 base16g-crc16-rs files=1", "", "LM001 PAYLOAD #ABCD"]
    )
    assert rows == ["LM001 PAYLOAD #ABCD"]


def test_build_training_rows_pairs_each_image_with_its_own_text():
    pages = [["#!glyphive banner", "LM001 AAA #AA", "LM002 BBB #BB"]]

    def slice_page(page_index, n_rows):
        return [Path(f"/rows/p{page_index}_r{i}.png") for i in range(n_rows)]

    rows = build_training_rows(pages, slice_page)
    assert [r.text for r in rows] == ["LM001 AAA #AA", "LM002 BBB #BB"]
    # the banner is excluded from the ground truth, so the FIRST image must
    # correspond to the first *frame* row -- this is the off-by-one that
    # silently mispaired every row on 2026-07-21
    assert rows[0].image.name == "p0_r0.png"


def test_build_training_rows_refuses_a_slicer_that_disagrees_on_row_count():
    """A geometric row estimate over-counted 78 vs a real 58 and walked off the page."""
    pages = [["LM001 AAA #AA", "LM002 BBB #BB"]]

    def bad_slice(page_index, n_rows):
        return [Path(f"/rows/r{i}.png") for i in range(n_rows + 1)]

    with pytest.raises(ValueError, match="disagree"):
        build_training_rows(pages, bad_slice)


# --------------------------------------------------------------------------- #
# The pairing gate
# --------------------------------------------------------------------------- #
def _rows(n, text_for):
    return [GroundTruthRow(Path(f"/rows/r{i}.png"), text_for(i), 0, i) for i in range(n)]


def test_verify_pairs_passes_on_correctly_paired_data():
    rows = _rows(40, lambda i: f"LM{i:03d} PAYLOAD{i} #AB")
    result = verify_pairs(rows, lambda p: _text_of(rows, p))
    assert result.ok
    assert "OK" in result.describe()


def _text_of(rows, path):
    for row in rows:
        if row.image == path:
            return row.text
    raise AssertionError(path)


def test_verify_pairs_catches_an_off_by_one_shift():
    """The exact 2026-07-21 bug: every image shows the NEXT row's text."""
    rows = _rows(40, lambda i: f"LM{i:03d} PAYLOAD{i} #AB")
    shifted = {row.image: rows[min(i + 1, len(rows) - 1)].text
               for i, row in enumerate(rows)}
    result = verify_pairs(rows, lambda p: shifted[p])
    assert not result.ok
    assert "wrong text" in result.describe()


def test_verify_pairs_tolerates_mid_line_ocr_noise():
    """CRC/RS absorb a misread character; only a wrong PREFIX means mispairing."""
    rows = _rows(30, lambda i: f"LM{i:03d} PAYLOADPAYLOAD{i} #AB")

    def noisy(path):
        text = _text_of(rows, path)
        return text[:20] + "X" + text[21:]

    assert verify_pairs(rows, noisy).ok


def test_verify_pairs_reports_offenders_by_name():
    rows = _rows(20, lambda i: f"LM{i:03d} PAYLOAD{i} #AB")
    result = verify_pairs(rows, lambda p: "COMPLETELY DIFFERENT TEXT")
    assert not result.ok
    assert result.mismatched[0][0].endswith(".png")
    assert result.sampled == 20


def test_verify_pairs_handles_empty_input():
    assert verify_pairs([], lambda p: "").ok


# --------------------------------------------------------------------------- #
# Pipeline gates (pure; no toolchain required)
# --------------------------------------------------------------------------- #
from glyphive.training import (  # noqa: E402
    StageError,
    check_encoding,
    check_training_data,
    run_stage,
    summarize,
    write_box_file,
)
from glyphive.training.pipeline import PipelineResult  # noqa: E402


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_stage_returns_output_on_success():
    out = run_stage("x", ["true"], runner=lambda *a, **k: _Completed(0, "hi", ""))
    assert "hi" in out


def test_run_stage_raises_with_the_real_error_text():
    """Swallowing stderr is how a long run ends in an unexplained empty dir."""
    with pytest.raises(StageError, match="boom"):
        run_stage("x", ["false"],
                  runner=lambda *a, **k: _Completed(1, "", "line1\nboom"))


def test_check_encoding_aborts_on_unencodable_transcriptions():
    log = "Can't encode transcription: 'LM?Y' in language ''\nskip ratio=35.000%"
    with pytest.raises(StageError, match="could not be encoded"):
        check_encoding(log)


def test_check_encoding_aborts_on_a_nonzero_skip_ratio():
    """A 35% silent skip once trained a model on two thirds of its data."""
    with pytest.raises(StageError, match="skip ratio"):
        check_encoding("At iteration 1/20/20, skip ratio=12.500%")


def test_check_encoding_accepts_a_clean_run():
    failures, skip = check_encoding("At iteration 20/20/20, skip ratio=0.000%")
    assert failures == 0 and skip == 0.0


def test_check_training_data_rejects_too_few_rows():
    with pytest.raises(StageError, match="at least"):
        check_training_data([], lambda p: "", minimum_rows=10)


def test_check_training_data_rejects_mispaired_rows():
    rows = [GroundTruthRow(Path(f"/r{i}.tif"), f"LM{i:03d} DATA{i} #AB", 0, i)
            for i in range(120)]
    with pytest.raises(StageError, match="mispaired|wrong text"):
        check_training_data(rows, lambda p: "TOTALLY DIFFERENT LINE")


def test_check_training_data_accepts_correctly_paired_rows():
    rows = [GroundTruthRow(Path(f"/r{i}.tif"), f"LM{i:03d} DATA{i} #AB", 0, i)
            for i in range(120)]
    lookup = {r.image: r.text for r in rows}
    check_training_data(rows, lambda p: lookup[p])  # must not raise


def test_write_box_file_carries_ground_truth_not_ocr(tmp_path):
    """lstmbox labels boxes with what stock OCR READ; that trains in its errors."""
    box = Path(str(tmp_path)) / "row.box"
    write_box_file(box, "LM001 PAYLOAD #AB", 2550, 52)
    content = box.read_text(encoding="utf-8")
    assert "WordStr" in content
    assert "LM001 PAYLOAD #AB" in content


def test_summary_labels_cer_as_a_proxy_and_warns():
    plan = _plan()
    result = PipelineResult(
        model_path=plan.model_path, rows_trained=1600, rows_held_out=300,
        encode_failures=0, skip_ratio=0.0, cer_proxy=2.081,
        gate_verdict="UNGATED",
    )
    summary = summarize(plan, result)
    caveat = summary["cer_is_a_proxy"].lower()
    assert "does not predict restore" in caveat
    assert "byte-identical restore" in caveat
    assert "beaten stock" in summary["warning"]
    assert summary["cer_proxy_percent"] == 2.081
