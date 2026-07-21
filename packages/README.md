# glyphive companion packages

Opt-in distributions that extend glyphive without adding weight to the core
wheel. Each builds an independent wheel; the core `glyphive` package never
depends on them.

## OCR model packages (`ocrmodel-<font>`)

One distribution per fine-tuned OCR model, e.g. `glyphive-ocrmodel-dejavu`.
Each ships a `.traineddata` fine-tuned for one font and registers a
`tesseract-glyphive-<font>` provider via the `glyphive.ocr_providers` entry
point. Users install only the model(s) they want; absence falls back to the
core stock engine.

The LSTM model format is engine-version independent (Tesseract 4.x/5.x).

> **Status: experimental, and CER is not the acceptance metric.** A held-out
> CER of 0.000% does *not* predict a successful restore: models trained on
> unframed alphabet strings scored ~0% CER and still failed real page restore,
> because they never saw the frame boundaries, inter-line bleed, and page-level
> segmentation that real pages have. Worse, on the default `base16g` channel a
> model buys nothing over stock Tesseract + the character whitelist — the
> per-line CRC and Reed-Solomon layers already absorb stock's residual noise.
> **The only acceptance gate is a byte-identical end-to-end restore**
> (create → rasterize → OCR → extract → diff), recorded in
> `benchmarks/results/`; report CER only as a labelled proxy. A model is worth
> shipping only for a channel where it demonstrably beats that stock baseline
> (measured: `base32g` needs one, `base16g` does not).

### Adding another font

Copy `ocrmodel-dejavu/` to `ocrmodel-<font>/` and change, in this order:
1. distribution name `glyphive-ocrmodel-<font>` (pyproject `[project].name`),
2. the package dir `src/glyphive_ocrmodel_<font>/`,
3. the provider `name` / class and the `_MODEL_LANG` (`glyphive<font>`),
4. the entry-point line `tesseract-glyphive-<font> = ...`,
5. the README provenance (base-model version/SHA, font license).

The provider is otherwise identical — same base16g whitelist and PSM, only the
bundled model and its language name differ.

### Model file

The `.traineddata` binaries are **not** in source control; they are produced by
the reproducible VM recipe in `benchmarks/training/` and added as package data
(`artifacts = [...]`) only at release-build time. Publishing a model wheel is a
deliberate per-model step gated on: (a) the model was trained on **framed**
ground truth (real `create` output — kind prefix, spaces, delimiter — not raw
alphabet strings), (b) it passes a byte-identical end-to-end restore gate,
recorded in `benchmarks/results/` with provenance, and beats the stock
`tesseract-glyphive` baseline on that gate for the channel it targets, and
(c) its base-model license/version/SHA are recorded in its README.
