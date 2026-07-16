# glyphive

Glyphive archives a file tree to **compact, OCR-friendly, printable pages** and
restores it from a transcript or scan. Its format is designed for the failure
modes of printed text: every encoded line is independently checked, page
metadata is protected, and Reed-Solomon parity can repair scattered OCR errors.

The pages stay readable and re-typeable. Restore never accepts a guess merely
because decompression progressed; checksums, error correction, and the final
document digest are the correctness oracles.

> **Alpha:** the current format and CLI may change before 1.0.

## Installation

```bash
pip install glyphive
```

| Extra | Adds | Needed for |
| --- | --- | --- |
| `pdf` | `fpdf2` | PDF output |
| `docx` | `python-docx` | Word output |
| `zstd` | `zstandard` | zstd compression |
| `ocr` | `Pillow`, `pytesseract` | Tesseract image bridge |
| `all` | all packages above | All lightweight integrations |

The OCR extra does not install the operating-system Tesseract program or its
language data.

## 30-second tour

```bash
glyphive create -f backup.txt --compression gzip -C project .
glyphive list -f backup.txt
glyphive extract -f backup.txt -C restored
```

The create command archives one root directory. Root-level `.gitignore` and
`.ignore` files are honored by default, and `.git/` is always omitted. The list
and extract commands read the protected machine metadata rather than trusting
the human-facing header text.

## Learn more

- [Create an archive](guides/create.md) — formats, compression, metadata, and
  ignore behavior.
- [Restore an archive](guides/restore.md) — inspect, verify, and safely extract.
- [Wire format](guides/wire-format.md) — archive records, line frames, parity,
  and page metadata.
- [OCR](guides/ocr.md) — provider installation, selection, and measurement.
- [Benchmarks](benchmarks.md) — reproducible performance and density
  methodology.
- [API reference](api/overview.md) — the public Python modules.
- [Changelog](changelog.md) — user-visible changes.
