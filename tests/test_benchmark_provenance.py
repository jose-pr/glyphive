"""Benchmark results are committed evidence -- and must name no machine.

Two halves, matching the two ways the leak got in:

* the harness that *writes* provenance (``benchmarks/e2e_grid.py``) must have
  no code path that can emit a hostname or an address, including via an
  operator-supplied ``--host-label``; and
* every result file already in ``benchmarks/results/`` must stay clean, which
  is the half a harness test alone cannot cover -- the seven records redacted
  in 2026-08 were *hand-written* analysis JSONs, not harness output.

Test fixtures here use RFC 5737 documentation addresses on purpose: a test for
a leak must not itself be one.
"""

from __future__ import annotations

import ast
import json
import platform
import re
import socket
import sys
from pathlib import Path as _StdPath

import pytest

_REPO_ROOT = _StdPath(__file__).resolve().parent.parent
_BENCHMARKS_DIR = _REPO_ROOT / "benchmarks"
if str(_BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARKS_DIR))

import e2e_grid  # noqa: E402

RESULTS_DIR = _BENCHMARKS_DIR / "results"

# IPv4 literal.
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# user@host.tld -- the dotted right-hand side keeps codec alphabet strings
# such as "34@ABCDKLMPRTVXY" (a real value in ocr-tesseract411-current-plus5)
# from reading as an address.
_USER_AT_HOST = re.compile(r"\b[A-Za-z0-9_.-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# A per-user home directory, i.e. a path that only exists on one machine.
_HOME_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}|/home/)[A-Za-z0-9_.-]+", re.IGNORECASE
)

_IDENTITY_SCANS = (
    ("IPv4 address", _IPV4),
    ("user@host address", _USER_AT_HOST),
    ("per-user home path", _HOME_PATH),
)


# --------------------------------------------------------------------------- #
# The writer: redaction of operator-supplied labels
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        "bench-vm 203.0.113.7",
        "root@203.0.113.7 (Rocky 9)",
        "build box at 192.0.2.10",
        "ci@runners.example.com",
    ],
)
def test_redact_identity_removes_addresses(raw):
    cleaned = e2e_grid.redact_identity(raw)
    assert not _IPV4.search(cleaned), cleaned
    assert not _USER_AT_HOST.search(cleaned), cleaned
    assert e2e_grid.REDACTED in cleaned


def test_redact_identity_keeps_a_non_identifying_label_intact():
    # Only the identifying part is removed; the class descriptor survives, so
    # redaction never costs the reader the information they actually use.
    assert e2e_grid.redact_identity("bench-vm") == "bench-vm"
    assert e2e_grid.redact_identity("Rocky 9 Linux VM, 32 cores") == (
        "Rocky 9 Linux VM, 32 cores"
    )
    assert e2e_grid.redact_identity("bench-vm 203.0.113.7") == (
        f"bench-vm {e2e_grid.REDACTED}"
    )


def test_redact_identity_does_not_eat_a_codec_alphabet():
    alphabet = "34@ABCDKLMPRTVXY"
    assert e2e_grid.redact_identity(alphabet) == alphabet


# --------------------------------------------------------------------------- #
# The writer: machine CLASS, never machine identity
# --------------------------------------------------------------------------- #


def test_machine_class_records_class_not_identity():
    info = e2e_grid.machine_class()

    assert set(info) == {"label", "system", "release", "arch", "cpu_count"}
    assert info["label"] == e2e_grid.DEFAULT_HOST_LABEL
    assert info["system"] == platform.system()
    assert info["arch"] == platform.machine()
    assert isinstance(info["cpu_count"], int) and info["cpu_count"] >= 1

    blob = json.dumps(info)
    assert platform.node() not in blob
    assert socket.gethostname() not in blob


def test_machine_class_redacts_a_host_label_carrying_an_address():
    info = e2e_grid.machine_class("bench-vm 203.0.113.7")
    assert not _IPV4.search(json.dumps(info))
    assert "bench-vm" in info["label"]


def test_machine_class_falls_back_to_the_generic_label_when_given_nothing():
    assert e2e_grid.machine_class("")["label"] == e2e_grid.DEFAULT_HOST_LABEL


def test_the_harness_never_calls_a_hostname_api():
    """A structural guard: the leak returns the moment one of these is called.

    Parsed rather than grepped, so the docstring that *names* these APIs (to
    explain why they are absent) does not trip it.
    """
    tree = ast.parse((_BENCHMARKS_DIR / "e2e_grid.py").read_text(encoding="utf-8"))
    forbidden = {"node", "gethostname", "getfqdn", "gethostbyname"}

    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in forbidden:
            called.add(name)

    assert not called, f"hostname API called in e2e_grid.py: {sorted(called)}"


def test_build_result_provenance_carries_no_machine_identity():
    result = e2e_grid.build_result(
        "provenance-shape-check",
        [],
        repeat=1,
        corpus=[],
        host_label="bench-vm 203.0.113.7",
    )

    assert "machine" in result["provenance"]
    blob = json.dumps(result)
    for label, pattern in _IDENTITY_SCANS:
        assert not pattern.search(blob), f"{label} in harness output: {blob}"
    assert platform.node() not in blob
    assert socket.gethostname() not in blob


# --------------------------------------------------------------------------- #
# The record: every committed result file stays clean
# --------------------------------------------------------------------------- #


def _committed_result_files():
    return sorted(p for p in RESULTS_DIR.rglob("*") if p.is_file())


def test_there_are_result_files_to_check():
    assert _committed_result_files(), "results directory unexpectedly empty"


@pytest.mark.parametrize(
    "path", _committed_result_files(), ids=lambda p: p.relative_to(RESULTS_DIR).as_posix()
)
def test_committed_result_files_name_no_machine(path):
    """No hostname, address, or per-user path in checked-in evidence.

    Redacting a measurement's *provenance* is deliberate and is recorded in
    ``PROVENANCE.md``; the measurements themselves are never edited. If this
    fails on a new result, redact the record -- do not relax the pattern.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    for label, pattern in _IDENTITY_SCANS:
        found = sorted(set(pattern.findall(text)))
        assert not found, f"{label} in {path.name}: {found}"
