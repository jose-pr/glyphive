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

The fine-tuned models read the base16c channel at 0.000% CER (measured; see
the core repo's `benchmarks/results/ocr-training-sweep-20260718.json`), across
OCR-B, Liberation Mono, DejaVu Sans Mono, and Courier (via Nimbus Mono PS). The
LSTM model format is engine-version independent (Tesseract 4.x/5.x).

### Adding another font

Copy `ocrmodel-dejavu/` to `ocrmodel-<font>/` and change, in this order:
1. distribution name `glyphive-ocrmodel-<font>` (pyproject `[project].name`),
2. the package dir `src/glyphive_ocrmodel_<font>/`,
3. the provider `name` / class and the `_MODEL_LANG` (`glyphive<font>`),
4. the entry-point line `tesseract-glyphive-<font> = ...`,
5. the README provenance (base-model version/SHA, font license).

The provider is otherwise identical — same base16c whitelist and PSM, only the
bundled model and its language name differ.

### Model file

The `.traineddata` binaries are **not** in source control; they are produced by
the reproducible VM recipe in `benchmarks/training/` and added as package data
(`artifacts = [...]`) only at release-build time. Publishing a model wheel is a
deliberate per-model step gated on: (a) the model came from the held-out-CER
recipe, and (b) its base-model license/version/SHA are recorded in its README.
