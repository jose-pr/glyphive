# Restore an archive

Glyphive restores from an exact text transcript or from OCR output. The same
verification pipeline handles both: protected page metadata, per-line CRC,
Reed-Solomon correction, decompression, whole-document SHA-256, archive parsing,
and safe filesystem extraction.

## Inspect without extracting

```bash
glyphive list -f backup.txt
glyphive list -f backup.pdf --ocr-engine tesseract
```

The command decodes and verifies the document before printing the manifest. It
does not trust editable `#!glyphive` header prose.

Short command alias: `glyphive t`.

## Restore a transcript

```bash
glyphive extract -f backup.txt -C restored
```

The destination defaults to the current directory when `-C` is omitted. Restore
refuses to replace a differing existing file unless `--overwrite` is supplied:

```bash
glyphive extract -f backup.txt -C restored --overwrite
```

Short command alias: `glyphive x`.

The extractor rejects absolute archive paths, `..` traversal, paths that escape
through an existing symbolic link, duplicate/conflicting targets, and writes
outside the destination. It validates all targets before writing the first
entry.

## Restore from scans or generated documents

Install a Python bridge, document renderer, and the corresponding OCR engine:

```bash
pip install "glyphive[ocr,document-input]"
glyphive extract \
  -f scans/ \
  --ocr-engine tesseract \
  -C restored
```

Both `extract` and `list` accept a transcript, image, PDF, DOCX, or a directory
containing a mixture of those inputs. Direct child files are processed in
deterministic filename order. Each file is detected independently using content
signatures first, its extension second, and UTF-8 text as the fallback. This
allows extensionless or renamed common images and PDFs to be read correctly.

PDF pages are rendered with `pypdfium2`. DOCX page layout is rendered by
LibreOffice, which must provide `libreoffice` or `soffice` on `PATH`.
`--from-images` remains available as an explicit override when every supplied
file is an image. See [OCR](ocr.md).

## What failures mean

- **Missing page:** page footers show that the transcript is incomplete. The
  page is reported; it is not fabricated from guesses.
- **Line failed CRC:** the line is treated as a known erasure. Decode succeeds
  if Reed-Solomon parity can repair it within budget.
- **RS budget exceeded:** too much protected data is missing or damaged. Rescan
  or manually correct the named line.
- **Page hash mismatch:** the page's encoded data differs from its protected
  footer.
- **Document digest mismatch:** decoded/decompressed bytes do not match the
  authoritative SHA-256 in the machine header. Nothing is extracted.
- **Existing target conflict:** restore stops before mutation unless
  `--overwrite` is explicit.

Keep the original scan or transcript when repairing a failure. Avoid search
strategies that mutate characters merely because more compressed bytes become
readable; only the CRC, parity, page hashes, and final digest establish
correctness.
