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

- The current wire identifier is `g1`. Its payload alphabet is exactly
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

No public benchmark baseline has been recorded yet, so this release makes no
speed, compression-ratio, OCR-accuracy, or pages-per-archive claim. The manual
benchmark harness records min/median/max milliseconds per call; comparisons
use medians from matched environments. OCR density sweeps report both nominal
bytes per page and capacity adjusted for line-length erasures.

| Measurement | Previous release | Current evidence |
| --- | ---: | ---: |
| Create printable text | Not released | Baseline pending |
| Decode transcript | Not released | Baseline pending |
| `g1` codec encode/decode | Not released | Baseline pending |
| OCR-safe bytes per page | Not released | Multi-radix sweep pending |

**Target for the first release:** establish repeatable baselines without
regressing archive correctness. Density changes require a full print,
rasterize, OCR, and restore gate; a larger nominal radix alone is not evidence
of greater usable capacity.

### Validation state

| Check | State |
| --- | --- |
| Lightweight automated test suite | Must pass on the release commit |
| Package build and metadata check | Pending release validation |
| `mkdocs build --strict` | Passes locally; release CI gate still required |
| Public-file leakage scan | Pending release validation |
| Real OCR create/restore gate | Previously demonstrated for `none` and `zstd`; rerun required before release |
| Benchmark and alphabet-sweep baseline | Pending; no figures published |

The recorded OCR gate uses Courier 8pt rendered at 300 DPI and Tesseract 5.4.0,
then restores the fixture tree byte-for-byte. This is correctness evidence for
that measured channel, not a universal OCR-accuracy or performance claim.

### Publication state

Prepared but not published. No release tag has been pushed, and the first public
release remains gated on the validation above.
