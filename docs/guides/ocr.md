# OCR

OCR is an input adapter, not the correctness oracle. A provider turns a page
image into lines; Glyphive then accepts or rejects those lines using protected
metadata, CRC-16, Reed-Solomon correction, page hashes, and the final document
SHA-256.

## Providers

The registry includes `paddle`, `easyocr`, and `tesseract`. Automatic selection
prefers them in that order when installed and available.

| Provider | Python package | Notes |
| --- | --- | --- |
| `tesseract` | `glyphive[ocr]` | Installs Pillow and `pytesseract`; install the Tesseract executable and language data separately |
| `easyocr` | `easyocr` plus Pillow | Downloads/loads its own model assets according to EasyOCR configuration |
| `paddle` | `paddleocr` plus Pillow | Requires the compatible Paddle runtime/model setup |

Optional imports are lazy. Installing Glyphive without an OCR extra keeps text
create/restore usable and does not import model runtimes.

## CLI use

```bash
glyphive extract \
  -f scans/ \
  --ocr-engine tesseract \
  -C restored
```

Omit `--ocr-engine` to select the highest-preference available provider. The
CLI accepts one file or a directory of direct child files. It automatically
distinguishes UTF-8 transcripts, common images, PDFs, and DOCX documents; mixed
directories are processed in sorted order. `glyphive list` accepts the same
inputs and `--ocr-engine` option. Use `--from-images` only when an explicit
all-images override is useful.

`ocr_vote()` can combine line-level output from several engines, but its result
is only a candidate transcript. Agreement between engines is not proof; the
format's checks still decide whether restore is valid.

## Render documents for troubleshooting

The document conversion helper produces ordered PNG page images without
running OCR. DPI and Gaussian blur can be varied to reproduce scan conditions:

```bash
python tools/document_to_images.py backup.pdf pages/ --dpi 300 --blur 0.6
python tools/document_to_images.py backup.docx pages/ --dpi 240
```

Install `glyphive[document-input]` for PDF rendering. DOCX conversion also
requires LibreOffice on `PATH`. The tool prints each generated page path in
order, making its output suitable for inspection or a later OCR run.

## Print and scan guidance

- Disable text reflow, smart punctuation, and automatic line wrapping.
- Preserve page boundaries and scan the complete page, including `H`/`T`
  metadata.
- Start with the measured Courier 8pt / 300 DPI profile, then validate the
  actual printer, paper, scanner/camera, and OCR version you will rely on.
- Keep original scans. If a line exceeds the correction budget, rescan or
  manually transcribe the named frame rather than searching for a character
  change that merely improves decompression progress.

## Measure an alphabet

`tools/ocr_font_report.py` renders randomized character grids, rasterizes them,
runs a registered OCR provider, and reports safe glyphs plus page capacity. OCR
or rendering runs are compute-intensive and should be run on a suitable test
machine, not as part of the ordinary unit suite.

Compare standard radix candidates:

```bash
python tools/ocr_font_report.py \
  --font courier \
  --engine tesseract \
  --radix 16,32,64,85 \
  --size 8,9,11,12 \
  --dpi 300 \
  --rows 60 \
  --json courier-tesseract.json
```

`--charset` accepts named or literal candidate sets. Use `--extra-chars` to add
punctuation candidates without changing the base set, for example:

```bash
python tools/ocr_font_report.py \
  --font courier \
  --engine tesseract \
  --radix 64,85 \
  --extra-chars "*@#-^" \
  --rows 60 \
  --json punctuation.json
```

Merge reports from different engines or machines and recompute intersection
presets:

```bash
python tools/ocr_font_report.py \
  --merge courier-tesseract.json punctuation.json \
  --json merged.json
```

The report distinguishes nominal bytes per page from **erasure-adjusted usable
bytes per page**. A length-mismatched OCR line is an erasure and contributes no
usable payload; ranking by raw glyph count or nominal radix would overstate
capacity. Do not change `g1` from a sweep result alone: promote a candidate only
after repeat measurements and an end-to-end create, print/rasterize, OCR, and
byte-for-byte restore gate.
