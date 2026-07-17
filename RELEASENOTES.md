# Release Notes

Detailed release narrative, benchmark evidence, validation status, and
publication state live here. `CHANGELOG.md` remains the concise user-facing
history.

---

## [Unreleased]

Glyphive is preparing its first public release. The core workflow archives one
file tree to OCR-friendly text, PDF, or Word pages and restores it only after
line, page, and whole-document integrity checks pass.

### Behavior and compatibility

- The current wire identifier is `base16c-crc16-rs`. Its payload alphabet is exactly
  `ABCDHKLMPRTVXY34`; excluded glyphs are errors, not aliases.
- Reed-Solomon parity is interleaved across the document. It corrects scattered
  damaged lines within its budget, but it does not recreate a missing page.
- The archive stream is binary-safe and deterministic. Metadata profile `none`
  omits file mode and modification time; `basic` records permission bits and
  millisecond modification time.
- `pathlib_next>=0.8.1` is required. That release restores clean Python 3.9
  imports and correct local path walking on the supported Python range.
- The format and CLI remain alpha and may change before 1.0.

### Performance and density

One controlled VM sanity baseline has been recorded, but this release makes no
speed, compression-ratio, universal OCR-accuracy, or pages-per-archive claim.
The manual benchmark harness records min/median/max milliseconds per call; comparisons
use medians from matched environments. OCR density sweeps report both nominal
bytes per page and capacity adjusted for line-length erasures.

| Measurement | Previous release | Current evidence |
| --- | ---: | ---: |
| Create printable text | Not released | Not isolated by the current harness |
| Decode transcript | Not released | Not isolated by the current harness |
| `base16c-crc16-rs` codec encode/decode | Not released | VM sanity baseline recorded |
| OCR-safe bytes per page | Not released | 2,250 portable; 3,375 experimental Paddle-only |

**Target for the first release:** establish repeatable baselines without
regressing archive correctness. Density changes require a full print,
rasterize, OCR, and restore gate; a larger nominal radix alone is not evidence
of greater usable capacity.

### Validation state

| Check | State |
| --- | --- |
| Full automated test suite | CI run `29543951202` passed; latest Rocky Linux 9 VM run: 98 passed |
| Package build and metadata check | Pending release validation |
| `mkdocs build --strict` | Passes locally; release CI gate still required |
| Public-file leakage scan | Pending release validation |
| Real OCR create/restore gate | Current PDF/direct-input gate restored byte-for-byte on the Rocky Linux VM with Tesseract 4.1.1; release gate still required |
| Benchmark and alphabet-sweep baseline | VM timing sanity baseline and versioned OCR reports recorded; CI comparison pending |

The density reports use Courier 8 pt rendered at 300 DPI with default 36 pt
margins and 60-character rows. The portable 16-symbol alphabet produced zero
insertions and 2,250 usable bytes/page under both Tesseract 4.1.1 and PaddleOCR
3.7.0/PaddlePaddle 3.3.1. Adding `*@#-^` left Tesseract at radix 16 and reduced
usable capacity to 1,462.5 bytes/page. A Paddle-only 65-symbol superset measured
radix-64 density at 3,375 bytes/page with zero insertions, but no dense wire
preset or end-to-end restore gate exists yet.

The timing baseline used Python 3.9.25 on Rocky Linux 9. Median times were
21.65/49.45 ms for 1 KiB `base16c-crc16-rs` encode/decode and 299.07/734.10 ms for 16 KiB.
These are local VM sanity measurements, not CI performance evidence.

Historical Windows/Tesseract 5.4.0 probes are documented separately from the
versioned VM results. Courier 8 pt measured 16/16 safe symbols with no length
mismatch, while Courier 11 pt measured 15/16 and 2% mismatched lines. Older
font probes preserved frame indices for Courier and Consolas (12/12 each), but
not OCR-A Extended (0/12); Cascadia Mono's 0/12 was an embedding failure.
Those exploratory cells have no publishable capacity number, and OCR-B remains
unmeasured.

### Publication state

Prepared but not published. No release tag has been pushed, and the first public
release remains gated on the validation above.
