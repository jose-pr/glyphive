# glyphive-ocrmodel-dejavu

> **Status: experimental / do not rely on (2026-07-18).** This model was trained on unstructured character strings, not glyphive's real framed page layout, so it overfits and does NOT improve real restore — for the default `base16g` codec it is worse than plain Tesseract, which already restores real pages (validated to 4pt). Use `--ocr-engine tesseract-glyphive` (stock Tesseract + whitelist) instead. A corrected model, trained on framed data and gated on byte-for-byte restore, is future work.


An **opt-in** OCR model for [glyphive](https://github.com/jose-pr/glyphive),
fine-tuned for **DejaVu Sans Mono** renderings of the base16g alphabet.

```bash
pip install glyphive-ocrmodel-dejavu
glyphive extract -f scan/ --from-images --ocr-engine tesseract-glyphive-dejavu -C out
```

Installing it registers a `tesseract-glyphive-dejavu` OCR provider through the
`glyphive.ocr_providers` entry point. If the model file or Tesseract is not
present the provider reports itself unavailable and glyphive falls back to a
core engine — installing this package never breaks the stock path.

## Why

On the held-out training sweep (`benchmarks/results/ocr-training-sweep-20260718.json`
in the core repo) the fine-tuned model reads the base16g channel at **0.000% CER**
clean and blurred, versus ~4.6% for stock `eng`. The core document-wide
Reed-Solomon + per-line CRC already make the stock path restore correctly, so
this model is a robustness upgrade for marginal scans, not a requirement. 

**Multi-size (v0.2.0):** trained across 3/4/5/8/10/12pt renders, so one model reads the whole range — human-legible 10-12pt down to ultra-dense small fonts. Measured (base16g): **0% CER at 4pt through 12pt, clean and blurred** — `--font-size 4` is ~4x denser than the 8pt default, and stock OCR is unusable at 4pt (~40% CER). 3pt (~7x) reads clean but blur degrades for this monospace font; prefer 4pt as the dense floor. See the create guide's small-font section.

## The model file (not in source control)

The trained `glyphivedejavu.traineddata` (~15 MB) is **not** committed. It is
produced by the reproducible VM recipe in the core repo
(`benchmarks/training/measure_sweep.py` / `train_ocr_models.py`) and added as
package data only when a release wheel is built:

```toml
# uncomment in pyproject.toml [tool.hatch.build.targets.wheel] once present:
artifacts = ["src/glyphive_ocrmodel_dejavu/glyphivedejavu.traineddata"]
```

## Licensing

- **Base model:** a fine-tune of Tesseract `tessdata_best/eng` (Apache-2.0,
  redistributable). Exact base used: version `4.00.00alpha:eng:synth20170629 (tessdata_best; trained with Tesseract 5.4.1)`,
  SHA-256 `8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba`.
- **Training corpus:** synthetic — randomly generated base16g-alphabet lines
  rendered to images. DejaVu Sans Mono under the permissive DejaVu Fonts License (Bitstream Vera derivative). No reserved-font-name restriction on the synthetic training renders.
- **Engine compatibility:** the LSTM `.traineddata` format is shared across the
  Tesseract 4.x/5.x line, so one model file serves both (verified).
