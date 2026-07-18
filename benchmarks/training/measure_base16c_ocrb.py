#!/usr/bin/env python3
"""Decisive measurement: does a fine-tuned OCR-B base16c model beat stock?

Fixes the three failures the exploratory run hit (uniform-random + 1200 iters +
no CER gate): more ground-truth lines with a held-out split, --psm 6, many more
iterations, and an lstmeval CER on the WITHHELD set for both the stock eng model
and the fine-tune. Prints stock CER vs trained CER -- the number that decides
whether font-specific training is worth pursuing.
"""
import os
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path("/root/glyphive-ocr-training")
BEST = ROOT / "tessdata_best"
ENV = {**os.environ,
       "LD_LIBRARY_PATH": "/usr/local/lib:/usr/local/lib64",
       "PATH": "/usr/local/bin:" + os.environ.get("PATH", ""),
       "TESSDATA_PREFIX": str(BEST)}
CHARS = "ABCDHKLMPRTVXY34"
FONT = "OCR-B"
WORK = ROOT / "measure_base16c_ocrb"
GT = WORK / "gt"
N_TRAIN, N_EVAL = 200, 40
PT, DPI = 8, 300
ITERS = 6000


def run(cmd, **kw):
    return subprocess.run(cmd, env=ENV, text=True, capture_output=True, **kw)


def gen(split, n, rng):
    d = GT / split
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        line = "".join(rng.choice(CHARS) for _ in range(rng.randint(40, 70)))
        base = d / f"{split}_{i:04d}"
        base.with_suffix(".txt").write_text(line + "\n")
        run(["text2image", f"--text={base.with_suffix('.txt')}",
             f"--outputbase={base}", f"--font={FONT}", f"--ptsize={PT}",
             f"--resolution={DPI}", "--margin=10", "--leading=2",
             "--xsize=3600", "--ysize=200",
             "--degrade_image=false", "--rotate_image=false"])
        base.with_suffix(".gt.txt").write_text(line + "\n")


def lstmf_list(split):
    d = GT / split
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
    listf = WORK / f"{split}.txt"
    listf.write_text("\n".join(paths) + "\n")
    return listf, len(paths)


def eval_cer(traineddata_dir, model_lang, eval_list):
    """Return char error rate (%) of model on the eval lstmf set via lstmeval."""
    r = run(["lstmeval", "--model", str(traineddata_dir),
             "--traineddata", str(BEST / "eng.traineddata"),
             "--eval_listfile", str(eval_list)])
    out = (r.stdout or "") + (r.stderr or "")
    # Tesseract 5 lstmeval prints "BCER eval=X, BWER eval=Y" (older builds:
    # "Char error rate=X"). Accept either.
    for line in out.splitlines():
        for key in ("BCER eval=", "Char error rate="):
            if key in line:
                try:
                    return float(line.split(key)[1].split(",")[0])
                except Exception:
                    pass
    return None


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    rng = random.Random(4242)
    print("=== generating ground truth ===", flush=True)
    gen("train", N_TRAIN, rng)
    gen("eval", N_EVAL, rng)
    train_list, nt = lstmf_list("train")
    eval_list, ne = lstmf_list("eval")
    print(f"train lstmf={nt} eval lstmf={ne}", flush=True)
    if nt == 0 or ne == 0:
        print("FATAL: no lstmf produced"); return 1

    eng_lstm = WORK / "eng.lstm"
    if not eng_lstm.exists():
        run(["combine_tessdata", "-e", str(BEST / "eng.traineddata"), str(eng_lstm)])

    # Stock baseline CER on the held-out eval set (eng model, no fine-tune).
    stock = eval_cer(str(eng_lstm), "eng", eval_list)
    print(f"STOCK eng CER on eval = {stock}", flush=True)

    print(f"=== fine-tuning {ITERS} iterations ===", flush=True)
    model_base = WORK / "base16c_ocrb"
    r = run(["lstmtraining", "--continue_from", str(eng_lstm),
             "--model_output", str(model_base),
             "--traineddata", str(BEST / "eng.traineddata"),
             "--train_listfile", str(train_list),
             "--eval_listfile", str(eval_list),
             "--max_iterations", str(ITERS)])
    print(r.stdout[-2000:], flush=True)
    print("STDERR tail:", r.stderr[-800:], flush=True)

    ckpt = Path(f"{model_base}_checkpoint")
    if not ckpt.exists():
        print("no checkpoint"); return 1
    trained_cer = eval_cer(str(ckpt), "trained", eval_list)
    print(f"\n=== RESULT ===")
    print(f"STOCK eng CER on held-out eval  = {stock}")
    print(f"TRAINED base16c_ocrb CER on eval = {trained_cer}")
    print("=== done ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
