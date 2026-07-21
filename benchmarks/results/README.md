# Raw benchmark results

Files in this directory are machine-readable diagnostic evidence. They are not
CI performance claims unless their own metadata explicitly says otherwise.

- `compression-candidates-20260716.json` compares gzip, zstd, XZ, bzip2, and
  Brotli profiles on deterministic source, text, mixed, and already-compressed
  corpora. It records exact compressed sizes, `base16c-crc16-rs` page counts, median times,
  environment versions, trial count, and determinism status.
- `font-model-sweeps/` contains raw font, OCR-model, alphabet, and layout
  diagnostics. See its own README and the font candidate ledger.
- `PROVENANCE.md` describes the retained benchmark provenance policy.
- `e2e-grid-*.json` files are produced by `benchmarks/e2e_grid.py`, the
  repo-resident create -> rasterize -> OCR -> extract grid harness. Each cell
  reports a `status` of `restored` / `not-restored` / `not-built` / `error`;
  `not-built` (the create CLI itself refused, e.g. a geometry refusal) is
  always excluded from restore-rate denominators and never silently folded
  into a failure count. See `benchmarks/e2e_grid.py`'s module docstring for
  why this distinction exists.

Use CI evidence for release or performance claims. Re-run diagnostics on the
target environment before using them to make format decisions.
