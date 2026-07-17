# Benchmarks

Glyphive has two manual measurement tracks:

1. operation timing for archive creation, codec work, and restore; and
2. print/OCR density across font, size, engine, resolution, and alphabet.

The repository includes one controlled VM sanity baseline and four OCR reports.
These results document the current implementation and guide experiments; they
are not CI performance evidence or universal OCR claims.

## Operation timing

Run the benchmark harness explicitly:

```bash
python benchmarks/run.py
```

Save one structured result set:

```bash
python benchmarks/run.py --save --name baseline
```

Saved JSON lives under `benchmarks/results/` and records the project version,
Python/runtime environment, source revision, benchmark configuration, and one
result per case. Each metric uses repeated samples and reports
min/median/max milliseconds per call. Compare medians; a single average hides
warm-up effects and run-to-run noise.

For a meaningful before/after comparison:

- use the same machine, OS, Python version, installed extras, input fixture,
  sample count, and benchmark command;
- run both revisions close together and repeat if the result is near the noise
  floor;
- retain raw JSON rather than copying only a multiplier;
- use CI evidence for public release claims; local/manual runs are diagnostic;
- never mix OCR time into codec timing unless the benchmark is explicitly an
  end-to-end OCR case.

### Recorded VM sanity baseline

The first timing run used CPython 3.9.25 on a Rocky Linux 9 VM. It measured
commit `69dcb0f` with five repeats after one warm-up call:

| Workload | Min | Median | Max |
| --- | ---: | ---: | ---: |
| `g1` encode, 1 KiB | 21.58 ms | 21.65 ms | 22.75 ms |
| `g1` decode, 1 KiB | 49.04 ms | 49.45 ms | 50.87 ms |
| `g1` encode, 16 KiB | 293.54 ms | 299.07 ms | 306.92 ms |
| `g1` decode, 16 KiB | 727.13 ms | 734.10 ms | 740.08 ms |
| paginate pre-encoded 16 KiB | 0.74 ms | 0.77 ms | 0.78 ms |

This is a local VM sanity result, not release-grade performance evidence. A
matched CI before/after comparison is still required for a performance claim.

## OCR density

Run `tools/ocr_font_report.py` for selected font/engine cells. It supports
standard `--radix 16,32,64,85` candidates, named or literal `--charset` values,
and `--extra-chars "*@#-^"` for punctuation experiments. Save each run with
`--json`, then combine comparable cells with `--merge`.

Report both:

- **nominal bytes/page:** page geometry multiplied by effective bits per safe
  character; and
- **usable bytes/page:** nominal capacity adjusted by the observed rate of
  length-mismatched lines, which become erasures.

The tool chooses the largest power-of-two radix supported by the measured-safe
subset. A larger candidate alphabet can still produce lower usable capacity if
more lines are dropped, inserted, or rendered too wide. A result marked as
wrapping is invalid, not a density measurement.

### Recorded alphabet sweep

These cells used Courier 8 pt at 300 DPI, default 36 pt margins, and
60-character rows on the same Rocky Linux 9 VM. The portable and punctuation
cells used 60 rows; the dense Paddle superset used 150 rows.

| OCR engine | Candidate | Safe symbols | Radix | Insertions | Usable bytes/page |
| --- | --- | ---: | ---: | ---: | ---: |
| Tesseract 4.1.1 | `ABCDHKLMPRTVXY34` | 16 | 16 | 0% | 2,250 |
| PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 | `ABCDHKLMPRTVXY34` | 16 | 16 | 0% | 2,250 |
| Tesseract 4.1.1 | current 16 plus `*@#-^` | 17 | 16 | 35% | 1,462.5 |
| PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 | 65-symbol superset | 65 | 64 | 0% | 3,375 |

The punctuation experiment did not increase the portable radix: only `@` and
`-` joined the measured-safe subset, while line insertion losses reduced usable
capacity. The Paddle-only result supports further radix-64 work. A conservative
64-symbol candidate should omit visually confusable `O`; it is not yet a wire
preset and has not passed an end-to-end dense create/OCR/restore gate.

## Publication checklist

A table may move from raw reports into release notes only when it includes:

- source revision and package version;
- OS, Python, renderer, font, size, DPI, and OCR engine/version as applicable;
- sample/row counts and input fixture;
- min/median/max for timings or nominal plus erasure-adjusted capacity for OCR;
- a matched previous/current comparison; and
- an end-to-end restore check for any proposed wire-format or alphabet change.

See the [raw-result provenance](../benchmarks/results/PROVENANCE.md) and
[OCR guide](guides/ocr.md) for evidence and runnable alphabet-sweep commands.
