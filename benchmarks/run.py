#!/usr/bin/env python3
"""Structured, reproducible core benchmarks for Glyphive.

The suite deliberately excludes compression, rendering, filesystem traversal,
and OCR. It measures deterministic in-memory work at the format boundary:

* ``base16g-crc16-rs`` encode/decode for fixed 1 KiB and 16 KiB payloads.
* layout pagination over the already-encoded 16 KiB workload.

Each metric gets one warmup call, then ``repeat`` samples containing a fixed
number of calls. Results are reported as min/median/max milliseconds per call.

    python benchmarks/run.py
    python benchmarks/run.py --save
    python benchmarks/run.py --save --name glyphive-candidate-py314

Glyphive must be importable, either from an editable install or via
``PYTHONPATH=src``.
"""

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import timeit
import typing as ty
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version

from pathlib_next import Path

import glyphive
from glyphive.codec import Base16GCodec
from glyphive.layout import paginate


REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).resolve().parent / "results"

REPEAT = 5
WARMUP_CALLS = 1
SMALL_BYTES = 1024
MEDIUM_BYTES = 16 * 1024
LINE_WIDTH = 60
PARITY_RATIO = 0.12
LINES_PER_PAGE = 48

INNER_COUNTS = {
    "codec.base16g.encode_1k": 10,
    "codec.base16g.decode_1k": 10,
    "codec.base16g.encode_16k": 2,
    "codec.base16g.decode_16k": 2,
    "layout.paginate_16k": 50,
}


def deterministic_payload(size: int) -> bytes:
    """Return stable, non-random bytes without setup I/O."""
    return bytes((index * 73 + index // 7 + 19) & 0xFF for index in range(size))


def sample(
    fn: ty.Callable[[], ty.Any], inner: int, repeat: int = REPEAT
) -> ty.Dict[str, float]:
    """Return min/median/max milliseconds per call over repeated samples."""
    for _ in range(WARMUP_CALLS):
        fn()
    per_call = [
        timeit.timeit(fn, number=inner) / inner * 1000 for _ in range(repeat)
    ]
    return {
        "median_ms": round(statistics.median(per_call), 4),
        "min_ms": round(min(per_call), 4),
        "max_ms": round(max(per_call), 4),
    }


def _layout_meta(payload: bytes) -> ty.Dict[str, ty.Any]:
    return {
        "v": 1,
        "codec": "base16g-crc16-rs",
        "comp": "none",
        "files": 1,
        "bytes": len(payload),
        "pages": 1,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def measure() -> ty.Dict[str, ty.Dict[str, float]]:
    """Build fixtures once, validate them, then measure only target work."""
    codec = Base16GCodec()
    small = deterministic_payload(SMALL_BYTES)
    medium = deterministic_payload(MEDIUM_BYTES)
    small_lines = codec.encode(
        small, line_width=LINE_WIDTH, parity_ratio=PARITY_RATIO
    )
    medium_lines = codec.encode(
        medium, line_width=LINE_WIDTH, parity_ratio=PARITY_RATIO
    )

    if codec.decode(small_lines) != small or codec.decode(medium_lines) != medium:
        raise RuntimeError("benchmark fixture failed the base16g-crc16-rs round-trip check")

    layout_meta = _layout_meta(medium)

    def paginate_medium() -> ty.Any:
        # paginate writes the final page count into metadata, so every timed call
        # receives a fresh shallow copy while reusing the precomputed codec lines.
        return paginate(
            medium_lines, dict(layout_meta), lines_per_page=LINES_PER_PAGE
        )

    pages = paginate_medium()
    if not pages or sum(len(page.encoded_lines) for page in pages) != len(medium_lines):
        raise RuntimeError("benchmark fixture failed the pagination check")

    workloads = {
        "codec.base16g.encode_1k": lambda: codec.encode(
            small, line_width=LINE_WIDTH, parity_ratio=PARITY_RATIO
        ),
        "codec.base16g.decode_1k": lambda: codec.decode(small_lines),
        "codec.base16g.encode_16k": lambda: codec.encode(
            medium, line_width=LINE_WIDTH, parity_ratio=PARITY_RATIO
        ),
        "codec.base16g.decode_16k": lambda: codec.decode(medium_lines),
        "layout.paginate_16k": paginate_medium,
    }
    return {
        name: sample(fn, INNER_COUNTS[name]) for name, fn in workloads.items()
    }


def _git_output(*args: str) -> ty.Optional[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def git_metadata() -> ty.Dict[str, ty.Any]:
    """Return commit identity and worktree state when Git is available."""
    commit = _git_output("rev-parse", "HEAD")
    status = _git_output("status", "--porcelain", "--untracked-files=normal")
    return {
        "commit": commit,
        "dirty": None if status is None else bool(status),
    }


def _distribution_version(distribution: str) -> ty.Optional[str]:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def build_result(
    name: str, metrics: ty.Dict[str, ty.Dict[str, float]]
) -> ty.Dict[str, ty.Any]:
    small = deterministic_payload(SMALL_BYTES)
    medium = deterministic_payload(MEDIUM_BYTES)
    return {
        "schema_version": 1,
        "name": name,
        "glyphive_version": glyphive.__version__,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "dependencies": {
            "pathlib_next": _distribution_version("pathlib_next"),
            "reedsolo": _distribution_version("reedsolo"),
        },
        "git": git_metadata(),
        "iterations": {
            "warmup_calls": WARMUP_CALLS,
            "repeat": REPEAT,
            "inner": INNER_COUNTS,
        },
        "workloads": {
            "payloads": {
                "1k": {
                    "bytes": len(small),
                    "sha256": hashlib.sha256(small).hexdigest(),
                },
                "16k": {
                    "bytes": len(medium),
                    "sha256": hashlib.sha256(medium).hexdigest(),
                },
            },
            "codec": {
                "name": "base16g-crc16-rs",
                "line_width": LINE_WIDTH,
                "parity_ratio": PARITY_RATIO,
            },
            "layout": {
                "input": "precomputed base16g-crc16-rs 16k lines",
                "lines_per_page": LINES_PER_PAGE,
            },
        },
        "metrics": metrics,
    }


def main(argv: ty.Optional[ty.Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Glyphive core benchmarks")
    parser.add_argument(
        "--save",
        action="store_true",
        help="write result to benchmarks/results/",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="result name (default glyphive-<ver>-py<ver>)",
    )
    args = parser.parse_args(argv)

    pyver = f"py{sys.version_info.major}{sys.version_info.minor}"
    name = args.name or f"glyphive-{glyphive.__version__}-{pyver}"
    metrics = measure()
    result = build_result(name, metrics)

    print("=== Glyphive Benchmark ===")
    print(f"{name}  ({result['python']} on {result['processor']})")
    print(f"{'metric':25s} {'median':>10s} {'min':>10s} {'max':>10s}   (ms/call)")
    for key, metric in metrics.items():
        print(
            f"{key:25s} {metric['median_ms']:10.4f} "
            f"{metric['min_ms']:10.4f} {metric['max_ms']:10.4f}"
        )

    if args.save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output = RESULTS_DIR / f"{name}.json"
        output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"saved: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
