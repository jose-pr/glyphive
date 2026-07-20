# Recorded result provenance

The checked-in timing baseline was captured on a Rocky Linux 9 VM using CPython
3.9.25. Its JSON identifies commit `69dcb0f`, dependency versions, workload
digests, iteration counts, and complete min/median/max results. Treat it as a
local VM sanity baseline, not CI performance evidence.

The four OCR reports used Courier 8 pt at 300 DPI, default 36 pt margins, and
60-character rows. The portable and punctuation reports used 60 rows; the
dense Paddle superset used 150 rows. Engine versions were Tesseract 4.1.1 and
PaddleOCR 3.7.0 with PaddlePaddle 3.3.1.

| File | Purpose | Result |
| --- | --- | --- |
| `ocr-tesseract411-current16.json` | portable alphabet | radix 16, zero insertions, 2,250 usable bytes/page |
| `ocr-paddle370-current16.json` | portable alphabet cross-check | radix 16, zero insertions, 2,250 usable bytes/page |
| `ocr-tesseract411-current-plus5.json` | `*@#-^` experiment | 17 safe symbols, still radix 16, 35% insertions, 1,462.5 usable bytes/page |
| `ocr-paddle370-radix64-superset.json` | dense Paddle candidate | all 65 candidates safe, radix 64, zero insertions, 3,375 usable bytes/page |
| `ocr-e2e-framed-width60-20260719.json` | framed-model E2E gate, base16g width 60 | 8/5/4pt × clean/blur × {framed model, stock}: all byte-identical; framed models match but never beat stock |
| `ocr-e2e-rowsize-sweep-20260719.json` | row-size (line-width) sweep, base16g Courier | widths 60/75/90 restore byte-identical (stock and framed) at 8pt & 5pt; 90 is 50% past the 60 cap (needs `--force`); 105 refused by geometric page fit |
| `ocr-e2e-denser-and-4pt-20260719.json` | denser-codec + 4pt-max E2E, framed model vs stock | base16g 4pt restores at rows 60 AND max (212); base32g restores byte-identical at 60 and max (98) with a framed model; base64/base64g FAIL even trained (confusable glyphs `l`↔`1`, `;`↔`i` exceed CRC/RS budget) |

The two `ocr-e2e-*-20260719.json` files were captured on the Rocky 9 VM,
Tesseract 5.4.1 (built from source), at 300 DPI. Each row is a full
create → rasterize → OCR → `extract` → byte-diff of a small doc set; `result` is
`OK byte-identical` or a failure reason, `engine` is `framed` (per-font model) or
`stock` (stock Tesseract + base16g whitelist), and `model` names the actual
`.traineddata` used. The trained models are **not shipped** — see the
`glyphive-ocrmodel-*` notes; base16g restores under stock OCR with no model.

The dense Paddle alphabet measured was
`23456789ABCDEFGHIJKLMNOPQRSTUVWYZbdfhkmqgre.-:+=^!/*?&<>()[{}@%$#`.
For a future 64-symbol preset, omit visually confusable `O`. This preset is not
implemented and has not yet passed an end-to-end dense create, rasterize, OCR,
and restore gate.

Raw OCR reports predate embedding engine versions in the report schema; the
versions above come from the captured VM environment. Results describe only
this measured channel and should not be generalized across fonts, sizes,
renderers, scanners, or OCR releases.
