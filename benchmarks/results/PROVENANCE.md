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

The dense Paddle alphabet measured was
`23456789ABCDEFGHIJKLMNOPQRSTUVWYZbdfhkmqgre.-:+=^!/*?&<>()[{}@%$#`.
For a future 64-symbol preset, omit visually confusable `O`. This preset is not
implemented and has not yet passed an end-to-end dense create, rasterize, OCR,
and restore gate.

Raw OCR reports predate embedding engine versions in the report schema; the
versions above come from the captured VM environment. Results describe only
this measured channel and should not be generalized across fonts, sizes,
renderers, scanners, or OCR releases.
