# OCR model training for glyphive channels

Fine-tunes per-font, per-alphabet Tesseract 5 LSTM models so a document
rendered in a given font/alphabet can be read back more reliably than the
stock `eng` model manages (Tesseract's general model is trained on ordinary
mixed-font prose, not on OCR-B glyphs or glyphive's restricted alphabets).

Also produces reusable synthetic **ground-truth / eval data** per alphabet, so
the models and the data behind them can back regression tests and future
measurement work.

## What gets trained

Three alphabets × two fonts = six models:

| alphabet | characters | why |
| --- | --- | --- |
| `base16c` | `ABCDHKLMPRTVXY34` | the shipped codec alphabet |
| `base64`  | RFC 4648 `A-Za-z0-9+/` | a future higher-radix channel |
| `ascii`   | printable ASCII (`0x21`–`0x7E`) | general-purpose baseline |

Fonts: **OCR-B** (glyphive's bundled font) and **Liberation Mono** (a generic
monospace baseline; the core PDF "Courier" has no real screen-rendering TTF for
`text2image`, so Liberation Mono stands in as the generic-monospace comparison).

## Running it (on the training VM)

Rocky 9 ships tesseract 4.1.1 with no training tools and no `leptonica-devel`,
so both leptonica and tesseract 5.4.1 (with training tools) are built from
source. Building tesseract 5 also gives a version to compare against the OS 4.1.1.

```bash
# 1. one-time: build leptonica + tesseract 5.4.1 training tools, register the
#    OCR-B font with fontconfig, fetch tessdata_best/eng + training configs.
bash setup_vm.sh

# 2. train all six models (idempotent; --only base16c,base64 to limit).
python3 train_ocr_models.py
```

Outputs land in `/root/glyphive-ocr-training/work/<alphabet>_<font>/`:
`*.traineddata` (the model), the synthetic ground-truth `*.tif` / `*.gt.txt`
line pairs, and the per-line `*.lstmf` training files.

## Notes / gotchas found while building this

- **`text2image` resolves `--font` by fontconfig family name**, not by
  `--fonts_dir` alone — the font must be installed system-wide and the cache
  refreshed (`setup_vm.sh` does this).
- **Leptonica must be built with `libtiff-devel` present** or `text2image`
  writes the `.box` file but silently fails to write the `.tif`
  (`pixWriteTiff: function not present`).
- **`tesseract ... lstm.train` needs the `lstm.train` config** from the
  tesseract source tree's `tessdata/configs`, plus `TESSDATA_PREFIX` pointing
  at a dir that has both `eng.traineddata` and that `configs/` tree.
- Tesseract's training-tools build requires **pango / cairo / icu dev
  headers** at `./configure` time (checked there, not at link time) — without
  them `ENABLE_TRAINING` silently configures false and `make training` is a
  no-op that later fails on `training-install`.
