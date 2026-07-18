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

### Recovery headroom (`glyphive inspect`)

`glyphive inspect` reports how much damage a document can survive **without**
fully decoding or verifying it — so it works on a partially damaged scan that a
real restore would reject, and it never writes a file:

```bash
glyphive inspect -f backup.txt
glyphive inspect -f scans/ --json
```

It reads only the protected header and page footers, then reports the data and
parity page counts (whole-page recovery headroom, i.e. how many wholly lost
pages it can rebuild), the realized per-line Reed-Solomon budget (`nsym`
erasures per block, for scattered OCR damage), and which pages are present,
missing, or reconstructable. `--json` emits a machine-readable object.
`--strict` exits non-zero when the document is already unrecoverable (more data
pages missing than page-parity can rebuild); plain `inspect` always exits 0 on
a readable header. It is read-only and creates no files.

## Restore a transcript

```bash
glyphive extract -f backup.txt -C restored
```

The destination defaults to the current directory when `-C` is omitted. Restore
refuses to replace a differing existing file unless `--overwrite` is supplied:

```bash
glyphive extract -f backup.txt -C restored --overwrite
```

With `--overwrite` against an existing destination, each replaced file is
moved aside into a private backup before the new content lands. If publication
fails partway through (disk full, permission error, etc.), every already-
replaced file is restored from its backup and any newly created file is
removed, so the destination is never left half-migrated.

Short command alias: `glyphive x`.

The extractor rejects absolute archive paths, `..` traversal, paths that escape
through an existing symbolic link, duplicate/conflicting targets, and writes
outside the destination. Decompression is streamed into a private spool and
checked against the protected byte count and whole-document digest. Files are
then written into a private sibling staging directory; final paths are not
published until archive framing and every target have validated.
The transcript is parsed once into a normalized line spool. Glyphive indexes
line offsets rather than retaining line payloads, reconstructs data/parity into
spools, and corrects one Reed-Solomon codeword at a time.

For constrained systems, `--temp-dir PATH` selects spool placement,
`--chunk-size BYTES` tunes sequential I/O, and `--max-output-bytes BYTES` sets a
hard decompression ceiling. The protected archive size remains the default
ceiling when no explicit maximum is supplied.

`extract` logs progress as it runs (`staged`, then `published`, each with a
running count) rather than only a final summary line, rate-limited so a large
tree doesn't flood the log. `create` does the same for its own pipeline
stages (`archived`, `compressed`, `encoded`, `rendered`).

## Restore from scans or generated documents

Install a Python bridge, document renderer, and the corresponding OCR engine:

```bash
pip install "glyphive[ocr,document-input]"
glyphive extract \
  -f scans/ \
  --ocr-engine tesseract-glyphive \
  -C restored
```

`tesseract-glyphive` is an opt-in profile for Glyphive-generated pages. It uses
Tesseract page segmentation mode 6, restricts recognition to the measured
machine alphabet (`ABCDHKLMPRTVXY34#`), and disables its general-language
dictionaries. Use plain `tesseract` for unrestricted text or diagnostic scans;
its behavior is unchanged. On real photographed scans the constrained profile
restored substantially more documents than plain `tesseract`, so automatic
engine selection now prefers it over plain `tesseract` (Paddle, where
installed, still ranks first but needs model downloads and is unusable offline).

### De-scanning raw photos (`--descan`)

Raw phone photos are frequently too sharp/noisy for the frame CRC/RS to
recover — decode fails with `... failed CRC and exceeds RS correction budget`
even on an otherwise-good scan. A light Gaussian blur softens the glyph edges
enough for reliable OCR:

```bash
glyphive extract -f photos/ --from-images --descan 0.6 -C restored
```

`--descan` measured best around radius `0.6` on real scans. You can pass
several radii to try — `--descan 0,0.6,1.0` — in which case each image is OCR'd
at every radius and the CRC-valid lines are **merged across passes**: different
blurs recover different lines, and the per-line CRC makes combining them safe,
so a document no single blur can fully read may still restore from the union.
`--descan 0` (the default) applies no blur. It affects `--from-images` and
PDF/image auto-input only, never text transcripts.

Both `extract` and `list` accept a transcript, image, PDF, DOCX, or a directory
containing a mixture of those inputs. Direct child files are processed in
deterministic filename order. Each file is detected independently using content
signatures first, its extension second, and UTF-8 text as the fallback. This
allows extensionless or renamed common images and PDFs to be read correctly.

PDF pages are rendered with `pypdfium2`. Glyphive-generated DOCX paragraph
transcripts are read directly with `python-docx`, so restore does not require
Microsoft Word or LibreOffice. The separate image-conversion helper can produce
a diagnostic DOCX re-render, but it is not a Word-layout compatibility test.
`--from-images` remains available as an explicit override when every supplied
file is an image. See [OCR](ocr.md).

## Whole-page recovery (parity pages)

If the document was created with `--parity-pages K` (see the create guide),
`extract`/`list` reconstruct up to `K` wholly missing/unscannable data pages
from the document's dedicated parity pages before ordinary decode runs —
independent of whether the per-line Reed-Solomon budget on the surviving
pages would also have been enough. Reconstructed page numbers are reported
alongside the usual restore diagnostics. If more than `K` data pages are
missing, or too few parity pages survive, this layer cannot help and restore
falls back to the pre-parity-pages path: the codec's own document-wide RS
gets a chance to recover the gap from the surviving pages, and only fails
(naming the exceeded budget) if that is also insufficient. Parity pages
themselves carry no user data and are never reconstructed if lost — only
missing *data* pages are rebuilt.

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
