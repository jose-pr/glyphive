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

### Historical Windows font and size probes

Earlier exploratory probes ran on Windows with Tesseract 5.4.0. They predate
the versioned JSON report schema, so they guide follow-up experiments but are
not directly comparable to the Rocky VM table above. No trustworthy
bytes/page value survives for these probes: the early tool calculated capacity
from the full line width instead of the requested line length.

| Font / size | Render and sample | Observed result |
| --- | --- | --- |
| Courier 8 pt | 300 DPI; 60 rows x 110 characters; current 16-symbol alphabet | 16/16 symbols safe, 0% length-mismatched lines, no corrupting pairs |
| Courier 11 pt | 300 DPI; 60 rows x 60 characters; current 16-symbol alphabet | 15/16 symbols safe (`T` failed), 2% length-mismatched lines |
| Courier 11 pt | 300 DPI; 60 rows x 60 characters; all 36 uppercase alphanumerics | 26 zero-error symbols; detailed confusions were retained in the exploratory record |
| Courier / Consolas | 12 frame lines per font at 300 DPI | Frame index read correctly on 12/12 lines for each font |
| Cascadia Mono | 12 attempted frame lines at 300 DPI | 0/12: the tested font did not embed cleanly, so this is a rendering failure rather than an OCR score |
| OCR-A Extended | 12 frame lines at 300 DPI | 0/12 under Tesseract 5.4.0 |
| Courier 9/11/12/14 pt | exploratory 72-character frame lines at 300 DPI | Roughly one insertion per probe; only qualitative data was retained |
| Courier 16/18 pt | exploratory 72-character frame lines at 300 DPI | Lines wrapped at US-Letter width, invalidating the cells |
| Courier, 600 DPI | same exploratory channel as the 300-DPI probe | More insertions than at 300 DPI; only qualitative data was retained |

The historical Consolas recovery source was printed at 12 pt and scanned at
600 DPI. It was readable but suffered the familiar `0/O/o`, `1/l/I`, `2/Z`,
`5/S`, and `8/B` confusions; it was not a controlled capacity benchmark.
These results make Courier 8 pt at 300 DPI the current starting profile, not
proof that it is optimal for every renderer or OCR model. Subsequent VM sweeps
found a promising constrained OCR-B cell but it has not passed a complete
restore gate. In a 150-row layout diagnostic, 6 pt OCR-B with left alignment
and no added character spacing retained 16/16 symbols with no erasures and
yielded 5,050 usable bytes/page. Centering without spacing lost two symbols;
0.1 pt spacing recovered them but reduced capacity to 5,000 bytes/page.
Justification did not beat the left-aligned result and sometimes increased
erasures. These are constrained character-grid diagnostics, not CI performance
evidence. See the public
[font candidate ledger](https://github.com/jose-pr/glyphive/blob/master/benchmarks/results/FONT_CANDIDATES.md)
for exact files, model pins, measurements, and pending stroke/style tests.

## Publication checklist

A table may move from raw reports into release notes only when it includes:

- source revision and package version;
- OS, Python, renderer, font, size, DPI, and OCR engine/version as applicable;
- sample/row counts and input fixture;
- min/median/max for timings or nominal plus erasure-adjusted capacity for OCR;
- a matched previous/current comparison; and
- an end-to-end restore check for any proposed wire-format or alphabet change.

See the [raw-result provenance](https://github.com/jose-pr/glyphive/blob/master/benchmarks/results/PROVENANCE.md) and
[OCR guide](guides/ocr.md) for evidence and runnable alphabet-sweep commands.
