# Benchmarks

Glyphive has two manual measurement tracks:

1. operation timing for archive creation, codec work, and restore; and
2. print/OCR density across font, size, engine, resolution, and alphabet.

No public baseline is published yet. Until repeatable results are recorded,
this page documents the methodology and makes no performance or density claim.

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

## Publication checklist

A table may move from raw reports into release notes only when it includes:

- source revision and package version;
- OS, Python, renderer, font, size, DPI, and OCR engine/version as applicable;
- sample/row counts and input fixture;
- min/median/max for timings or nominal plus erasure-adjusted capacity for OCR;
- a matched previous/current comparison; and
- an end-to-end restore check for any proposed wire-format or alphabet change.

The first baseline is pending. See the [OCR guide](guides/ocr.md) for runnable
alphabet-sweep commands.
