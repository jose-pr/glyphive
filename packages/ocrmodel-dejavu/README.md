# glyphive-ocrmodel-dejavu

An **opt-in** OCR model for [glyphive](https://github.com/jose-pr/glyphive),
fine-tuned for **DejaVu Sans Mono** renderings of the base16c alphabet.

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
in the core repo) the fine-tuned model reads the base16c channel at **0.000% CER**
clean and blurred, versus ~4.6% for stock `eng`. The core document-wide
Reed-Solomon + per-line CRC already make the stock path restore correctly, so
this model is a robustness upgrade for marginal scans, not a requirement.

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

- The model is a fine-tune of Tesseract `tessdata_best/eng` (Apache-2.0,
  redistributable). Before publishing a wheel, record the exact base-model
  version and SHA-256 here.
- The training corpus is synthetic (randomly generated base16c lines rendered
  in DejaVu Sans Mono); DejaVu Sans Mono ships under the permissive DejaVu
  Fonts License. No reserved-font-name obligation applies.
- Engine compatibility: the LSTM `.traineddata` format is shared across the
  Tesseract 4.x/5.x line, so one model file serves both.
