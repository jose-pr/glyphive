# Restore an archive

Glyphive restores from an exact text transcript or from OCR output. The same
verification pipeline handles both: protected page metadata, per-line CRC,
Reed-Solomon correction, decompression, whole-document SHA-256, archive parsing,
and safe filesystem extraction.

## Inspect without extracting

```bash
glyphive list -f backup.txt
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

## Restore from an image

Install a Python bridge and the corresponding OCR engine, then identify image
input explicitly:

```bash
pip install "glyphive[ocr]"
glyphive extract \
  -f scan.png \
  --from-images \
  --ocr-engine tesseract \
  -C restored
```

The current CLI accepts one page image per invocation. The Python OCR API also
offers multi-image orchestration for applications that assemble page scans
before decode. See [OCR](ocr.md).

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
