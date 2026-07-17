# Raw font and OCR model sweeps

These JSON reports are the machine-readable evidence summarized in
[`FONT_CANDIDATES.md`](../FONT_CANDIDATES.md). They were produced on the Rocky
Linux VM with `tools/ocr_font_report.py`; they are diagnostic measurements,
not CI performance claims.

- `model-*` compares the pinned standard, best, and fast English Tesseract
  models with Courier 8 pt and bundled OCR-B 6 pt.
- `constrained-*` adds the exact candidate alphabet whitelist, page
  segmentation mode 6, and disabled system/frequency dictionaries.
- `font-*` records the external OCRAB and OCRA candidates with the system and
  pinned fast English models.
- `ocraI-fast.json`, `ocraIII-fast.json`, and `ocraIV-fast.json` complete the
  external OCRA repository history. Their filenames do **not** establish ISO
  OCR-B Size I/III/IV conformance.
- `ocraI-portable16-constrained.json`,
  `ocraIII-portable16-constrained.json`, and
  `ocraIV-portable16-constrained.json` test those external OCR-A files at 6.0,
  6.8, 9.1, and 10.2 pt. All were rejected; the point sizes do not turn the
  filenames into ISO OCR-B optical sizes.
- `layout-ocrb6.json` compares left, centered, and justified OCR-B rows with
  0.0--0.3 pt character spacing. This is a constrained character-grid
  diagnostic, not an end-to-end restore result or CI performance evidence.
- `layout-courier8.json` applies the same layout/tracking matrix to Courier
  8 pt. Its best cell was left or centered at 0 pt added spacing (4,125 usable
  bytes/page); OCR-B's left/0 cell remained denser at 5,050 bytes/page.
- `ocrb-sizes-*` is the earlier 60-row point-size sweep. Prefer the 150-row
  model reports for recommendations; the shorter sweep is retained so future
  work can explain rather than erase the conflicting result.

External font paths in the reports point to ephemeral VM locations. Exact
source revisions, file hashes, licenses, and Tesseract model hashes are in the
candidate ledger.
