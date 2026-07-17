#!/usr/bin/env python3
"""Fine-tune per-font, per-alphabet Tesseract LSTM models for glyphive.

Runs on the Rocky 9 training VM, where tesseract 5.4.1 + training tools have
been built from source (see build_and_train.sh for the leptonica/tesseract
build; this script owns everything from ground-truth generation onward).

For each (alphabet, font) pair it:
  1. generates synthetic ground-truth line images + .gt.txt via text2image,
  2. builds the per-line .lstmf training files,
  3. fine-tunes from tessdata_best/eng.traineddata,
  4. writes a .traineddata and records the eval CER.

Deliberately a single Python driver (not a bash loop): the shell version hit
too many quoting / process-substitution / flag-name pitfalls, and one Python
process is far cheaper to iterate on than many SSH round-trips. Everything is
idempotent -- re-running skips completed steps.

Alphabets: base16c (shipped codec), base64 (RFC 4648), ascii (printable).
Fonts: OCR-B (glyphive's bundled font) and a generic monospace baseline.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/root/glyphive-ocr-training")
TESSDATA_BEST = ROOT / "tessdata_best"
ENV = {
    **os.environ,
    "LD_LIBRARY_PATH": "/usr/local/lib:/usr/local/lib64",
    "PATH": "/usr/local/bin:" + os.environ.get("PATH", ""),
    # tesseract ... lstm.train loads eng.traineddata to bootstrap the LSTM
    # feature extractor; point it at the best-model dir we downloaded.
    "TESSDATA_PREFIX": str(TESSDATA_BEST),
}

ALPHABETS = {
    "base16c": "ABCDHKLMPRTVXY34",
    "base64": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
    "ascii": "".join(chr(c) for c in range(0x21, 0x7F)),  # printable, no space
}
# fontconfig family name -> friendly tag for filenames
FONTS = {
    "OCR-B": "ocrb",
    "Liberation Mono": "libmono",
}

N_LINES = 60
LINE_MIN, LINE_MAX = 40, 70
PTSIZE = 8
DPI = 300
MAX_ITERATIONS = 1200


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"+ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=cwd, env=ENV, check=check, text=True, capture_output=True)


def ensure_ground_truth(alphabet_name: str, chars: str, font: str, tag: str, work: Path) -> list[Path]:
    """Generate .tif/.gt.txt line-image ground truth via text2image."""
    gt_dir = work / "ground-truth"
    gt_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(gt_dir.glob("*.gt.txt"))
    if existing:
        return existing

    rng = random.Random(hash((alphabet_name, font)) & 0xFFFFFFFF)
    lstmf_inputs: list[Path] = []
    for i in range(N_LINES):
        length = rng.randint(LINE_MIN, LINE_MAX)
        line = "".join(rng.choice(chars) for _ in range(length))
        base = gt_dir / f"{tag}_{i:04d}"
        text_file = base.with_suffix(".txt")
        text_file.write_text(line + "\n", encoding="utf-8")
        # text2image renders one image + box per input text file.
        run([
            "text2image",
            f"--text={text_file}",
            f"--outputbase={base}",
            f"--font={font}",
            f"--ptsize={PTSIZE}",
            f"--resolution={DPI}",
            "--margin=10",
            "--leading=2",
            "--xsize=3600",
            "--ysize=200",
            "--degrade_image=false",  # clean synthetic render (matches glyphive PDFs)
            "--rotate_image=false",
        ])
        # tesstrain convention: line image + <name>.gt.txt transcription.
        (base.with_suffix(".gt.txt")).write_text(line + "\n", encoding="utf-8")
        lstmf_inputs.append(base)
    return sorted(gt_dir.glob("*.gt.txt"))


def build_lstmf(tag: str, work: Path) -> Path:
    """Create .lstmf files from the tif/box pairs and a listfile of them."""
    gt_dir = work / "ground-truth"
    listfile = work / f"{tag}.training_files.txt"
    lstmf_paths: list[str] = []
    for box in sorted(gt_dir.glob("*.box")):
        base = box.with_suffix("")
        tif = base.with_suffix(".tif")
        if not tif.exists():
            print(f"  ! no tif for {base.name}, skipping", flush=True)
            continue
        # tesseract produces <base>.lstmf from the tif+box pair.
        proc = run(
            [
                "tesseract",
                str(tif),
                str(base),
                "--psm",
                "13",
                "lstm.train",
            ],
            check=False,
        )
        lstmf = base.with_suffix(".lstmf")
        if lstmf.exists():
            lstmf_paths.append(str(lstmf))
        else:
            print(f"  ! lstmf not produced for {base.name}: {proc.stderr[-300:]}", flush=True)
    listfile.write_text("\n".join(lstmf_paths) + "\n", encoding="utf-8")
    return listfile


def train_one(alphabet_name: str, chars: str, font: str) -> dict:
    tag = f"{alphabet_name}_{FONTS[font]}"
    work = ROOT / "work" / tag
    work.mkdir(parents=True, exist_ok=True)
    result = {"tag": tag, "font": font, "alphabet": alphabet_name}

    print(f"\n=== {tag} ===", flush=True)
    ensure_ground_truth(alphabet_name, chars, font, tag, work)
    listfile = build_lstmf(tag, work)
    n_files = len([ln for ln in listfile.read_text().splitlines() if ln.strip()])
    result["training_files"] = n_files
    if n_files == 0:
        result["status"] = "no lstmf files generated"
        return result

    # Extract the base LSTM to fine-tune from.
    eng_lstm = work / "eng.lstm"
    if not eng_lstm.exists():
        run(["combine_tessdata", "-e", str(TESSDATA_BEST / "eng.traineddata"), str(eng_lstm)])

    model_base = work / f"{tag}"
    proc = run(
        [
            "lstmtraining",
            "--continue_from",
            str(eng_lstm),
            "--model_output",
            str(model_base),
            "--traineddata",
            str(TESSDATA_BEST / "eng.traineddata"),
            "--train_listfile",
            str(listfile),
            "--max_iterations",
            str(MAX_ITERATIONS),
        ],
        check=False,
    )
    print(proc.stdout[-1500:], flush=True)
    if proc.returncode != 0:
        result["status"] = "lstmtraining failed"
        result["stderr_tail"] = proc.stderr[-500:]
        return result

    checkpoint = Path(f"{model_base}_checkpoint")
    if not checkpoint.exists():
        result["status"] = "no checkpoint produced"
        return result

    final = work / f"{tag}.traineddata"
    run([
        "lstmtraining",
        "--stop_training",
        "--continue_from",
        str(checkpoint),
        "--traineddata",
        str(TESSDATA_BEST / "eng.traineddata"),
        "--model_output",
        str(final),
    ])
    result["status"] = "ok" if final.exists() else "stop_training produced no traineddata"
    result["traineddata"] = str(final)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated alphabet names to limit to")
    args = ap.parse_args()

    if not (TESSDATA_BEST / "eng.traineddata").exists():
        print("FATAL: tessdata_best/eng.traineddata missing", file=sys.stderr)
        return 1

    wanted = set(args.only.split(",")) if args.only else set(ALPHABETS)
    results = []
    for alphabet_name, chars in ALPHABETS.items():
        if alphabet_name not in wanted:
            continue
        for font in FONTS:
            try:
                results.append(train_one(alphabet_name, chars, font))
            except Exception as exc:  # noqa: BLE001
                results.append({"tag": f"{alphabet_name}_{FONTS[font]}", "status": f"exception: {exc}"})

    print("\n=== SUMMARY ===", flush=True)
    for r in results:
        print(f"  {r['tag']}: {r.get('status')} "
              f"(files={r.get('training_files', '?')})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
