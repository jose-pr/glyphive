# glyphive-ocrmodel-libmono

An **opt-in** OCR model for [glyphive](https://github.com/jose-pr/glyphive),
fine-tuned for **Liberation Mono** renderings of the base16g alphabet.

```bash
pip install glyphive-ocrmodel-libmono
glyphive extract -f scan/ --from-images --ocr-engine tesseract-glyphive-libmono -C out
```

Installing it registers a `tesseract-glyphive-libmono` OCR provider through the
`glyphive.ocr_providers` entry point. If the model file or Tesseract is not
present the provider reports itself unavailable and glyphive falls back to a
core engine — installing this package never breaks the stock path.

## Why

On the held-out training sweep (`benchmarks/results/ocr-training-sweep-20260718.json`
in the core repo) the fine-tuned model reads the base16g channel at **0.000% CER**
clean and blurred, versus ~4.6% for stock `eng`. The core document-wide
Reed-Solomon + per-line CRC already make the stock path restore correctly, so
this model is a robustness upgrade for marginal scans, not a requirement.

## The model file (not in source control)

The trained `glyphivelibmono.traineddata` (~15 MB) is **not** committed. It is
produced by the reproducible VM recipe in the core repo
(`benchmarks/training/measure_sweep.py` / `train_ocr_models.py`) and added as
package data only when a release wheel is built:

```toml
# uncomment in pyproject.toml [tool.hatch.build.targets.wheel] once present:
artifacts = ["src/glyphive_ocrmodel_libmono/glyphivelibmono.traineddata"]
```

## Licensing

- **Base model:** a fine-tune of Tesseract `tessdata_best/eng` (Apache-2.0,
  redistributable). Exact base used: version `4.00.00alpha:eng:synth20170629 (tessdata_best; trained with Tesseract 5.4.1)`,
  SHA-256 `8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba`.
- **Training corpus:** synthetic — randomly generated base16g-alphabet lines
  rendered to images. Liberation Mono under SIL OFL 1.1 (Reserved Font Name 'Liberation'). Only synthetic renders were used for training; no font file ships in this wheel.
- **Engine compatibility:** the LSTM `.traineddata` format is shared across the
  Tesseract 4.x/5.x line, so one model file serves both (verified).
