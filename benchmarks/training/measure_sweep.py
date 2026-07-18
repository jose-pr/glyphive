#!/usr/bin/env python3
"""Phase-4 closure sweep: held-out-gated fine-tune vs stock, across fonts, with
a clean AND a blurred (real-scan-proxy) held-out eval.

Reuses the validated recipe (--psm 6, train/eval split, lstmeval CER gate).
For each font on the shipped base16c alphabet:
  - build 200 train + 40 eval clean lines
  - build a SECOND eval set that is the same 40 lines rendered then Gaussian-
    blurred (sigma 0.8) to proxy scanner/camera degradation
  - measure stock eng CER and fine-tuned CER on BOTH eval sets
Emits one JSON with every cell so the plan can close on data, not a single cell.
"""
import json
import os
import random
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path("/root/glyphive-ocr-training")
BEST = ROOT / "tessdata_best"
ENV = {**os.environ,
       "LD_LIBRARY_PATH": "/usr/local/lib:/usr/local/lib64",
       "PATH": "/usr/local/bin:" + os.environ.get("PATH", ""),
       "TESSDATA_PREFIX": str(BEST)}
CHARS = "ABCDHKLMPRTVXY34"          # shipped base16c channel
FONTS = {"OCR-B": "ocrb", "Liberation Mono": "libmono", "DejaVu Sans Mono": "dejavu"}
N_TRAIN, N_EVAL = 200, 40
PT, DPI, ITERS, BLUR = 8, 300, 6000, 0.8


def run(cmd):
    return subprocess.run(cmd, env=ENV, text=True, capture_output=True)


def gen(d, font, n, rng, blur=False):
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        line = "".join(rng.choice(CHARS) for _ in range(rng.randint(40, 70)))
        base = d / f"l_{i:04d}"
        base.with_suffix(".txt").write_text(line + "\n")
        run(["text2image", f"--text={base.with_suffix('.txt')}",
             f"--outputbase={base}", f"--font={font}", f"--ptsize={PT}",
             f"--resolution={DPI}", "--margin=10", "--leading=2",
             "--xsize=3600", "--ysize=200",
             "--degrade_image=false", "--rotate_image=false"])
        base.with_suffix(".gt.txt").write_text(line + "\n")
        if blur:
            tif = base.with_suffix(".tif")
            if tif.exists():
                im = Image.open(tif).convert("L").filter(ImageFilter.GaussianBlur(BLUR))
                # text2image emits G4 bilevel (1 bit/sample); a blurred grayscale
                # image cannot be re-saved as G4. Write uncompressed grayscale TIFF
                # (same .tif extension the lstmf step consumes; tesseract reads it).
                im.save(tif, compression="raw")


def lstmf_list(d, listf):
    paths = []
    for box in sorted(d.glob("*.box")):
        base = box.with_suffix("")
        tif = base.with_suffix(".tif")
        if not tif.exists():
            continue
        run(["tesseract", str(tif), str(base), "--psm", "6", "lstm.train"])
        lf = base.with_suffix(".lstmf")
        if lf.exists():
            paths.append(str(lf))
    listf.write_text("\n".join(paths) + "\n")
    return listf, len(paths)


def cer(model, eval_list):
    r = run(["lstmeval", "--model", str(model),
             "--traineddata", str(BEST / "eng.traineddata"),
             "--eval_listfile", str(eval_list)])
    out = (r.stdout or "") + (r.stderr or "")
    for line in out.splitlines():
        for key in ("BCER eval=", "Char error rate="):
            if key in line:
                try:
                    return float(line.split(key)[1].split(",")[0])
                except Exception:
                    pass
    return None


def main():
    results = []
    for font, tag in FONTS.items():
        work = ROOT / f"sweep_{tag}"
        work.mkdir(parents=True, exist_ok=True)
        rng = random.Random(4242)
        print(f"=== {font} : ground truth ===", flush=True)
        gen(work / "train", font, N_TRAIN, rng)
        gen(work / "eval", font, N_EVAL, random.Random(777))
        gen(work / "eval_blur", font, N_EVAL, random.Random(777), blur=True)
        train_list, nt = lstmf_list(work / "train", work / "train.txt")
        eval_list, ne = lstmf_list(work / "eval", work / "eval.txt")
        blur_list, nb = lstmf_list(work / "eval_blur", work / "eval_blur.txt")
        print(f"{font}: train={nt} eval={ne} eval_blur={nb}", flush=True)
        if not (nt and ne and nb):
            results.append(dict(font=font, error="lstmf generation failed",
                                nt=nt, ne=ne, nb=nb))
            continue
        eng_lstm = work / "eng.lstm"
        run(["combine_tessdata", "-e", str(BEST / "eng.traineddata"), str(eng_lstm)])
        stock_clean = cer(eng_lstm, eval_list)
        stock_blur = cer(eng_lstm, blur_list)
        print(f"{font}: STOCK clean={stock_clean} blur={stock_blur}", flush=True)

        model_base = work / f"base16c_{tag}"
        print(f"=== {font} : fine-tune {ITERS} iters ===", flush=True)
        run(["lstmtraining", "--continue_from", str(eng_lstm),
             "--model_output", str(model_base),
             "--traineddata", str(BEST / "eng.traineddata"),
             "--train_listfile", str(train_list),
             "--eval_listfile", str(eval_list),
             "--max_iterations", str(ITERS)])
        ckpt = Path(f"{model_base}_checkpoint")
        trained_clean = cer(ckpt, eval_list) if ckpt.exists() else None
        trained_blur = cer(ckpt, blur_list) if ckpt.exists() else None
        row = dict(font=font, alphabet="base16c", iters=ITERS,
                   stock_clean_CER=stock_clean, trained_clean_CER=trained_clean,
                   stock_blur_CER=stock_blur, trained_blur_CER=trained_blur,
                   beats_stock_clean=(trained_clean is not None and stock_clean is not None
                                      and trained_clean < stock_clean),
                   beats_stock_blur=(trained_blur is not None and stock_blur is not None
                                     and trained_blur < stock_blur))
        results.append(row)
        print(f"RESULT {font}: {row}", flush=True)
    out = ROOT / "sweep_result.json"
    out.write_text(json.dumps(results, indent=2))
    print("=== SWEEP DONE ===", flush=True)
    print(json.dumps(results, indent=2), flush=True)


if __name__ == "__main__":
    sys.exit(main())
