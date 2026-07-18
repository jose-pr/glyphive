# Create an archive

`glyphive create` serializes one directory tree, compresses the complete archive
stream, encodes it as checked printable lines, paginates those lines, and writes
text, PDF, or Word output.

## Basic command

```bash
glyphive create -f backup.txt -C project .
```

`-C project .` means “archive the `project` directory as the root.” The alpha
CLI accepts exactly one root; put multiple inputs beneath a common directory.

The output format is inferred from `-f`: `.pdf` selects PDF, `.docx` selects
Word, and `.txt` or `.text` selects text. An unknown or missing extension falls
back to `text`, and an explicit `--format` always wins. Compression selects
`zstd` when the optional
dependency is available and otherwise uses `gzip`. Select behavior explicitly
when reproducibility across installations matters:

```bash
glyphive create \
  -f backup.txt \
  --format text \
  --codec base16c-crc16-rs \
  --compression gzip \
  --metadata none \
  -C project .
```

Short command alias: `glyphive c`.

## Output formats

```bash
pip install "glyphive[pdf]"
glyphive create -f backup.pdf --font courier --font-size 8 -C project .
```

```bash
pip install "glyphive[docx]"
glyphive create -f backup.docx --font Consolas --font-size 10 -C project .
```

PDF output accepts the built-in FPDF families `courier`, `helvetica`, `times`,
`symbol`, `zapfdingbats`, and `arial`, the bundled `ocr-b` and
`dejavu-sans-mono` options, or an existing `.ttf`/`.otf` path. Word output
accepts an installed Word font name (bundled fonts are embedded only in PDF;
DOCX references them by name, so a reader without the font installed sees a
substitute). `dejavu-sans-mono` is bundled under the permissive DejaVu Fonts
License and is the **shipped default** PDF font: it was one of only two fonts
(with Courier) that held up under the `tesseract-glyphive` profile in real
scan/restore testing and passed its byte-for-byte restore gate, so it is
preferred for recovery robustness even though Courier measures denser per page.
Pass `--font courier` for a zero-embedding, higher-density alternative (see the
font ledger for the full comparison and the default-font decision).
OCR-B is bundled under the SIL Open Font License 1.1. It is not the shipped
default, but `--font ocr-b --font-size 6` is a measured
`dense` preset: 5,050 usable bytes/page versus Courier 8pt's 4,125, and it
measured safe (0% character error, 0% line-insertion) on both Tesseract and
PaddleOCR with the project's `base16c-crc16-rs` alphabet — see
[`benchmarks/results/FONT_CANDIDATES.md`](https://github.com/jose-pr/glyphive/blob/master/benchmarks/results/FONT_CANDIDATES.md)
for the full matrix. Any other font must still be measured with the intended
OCR model before relying on it: a smaller font fits more characters on a
page, but it must be validated on the intended printer, scanner, resolution,
and OCR engine — nominal density is not the same as recoverable density.

The number of rows per page is calculated from the selected font size and page
geometry. Use `--minimal-margins` to reduce all margins from 36 points to 12
points and use more of the sheet. Confirm that the resulting printable area is
inside the limits of the intended printer and scanner before relying on it:

```bash
glyphive create -f dense.pdf --format pdf --font-size 8 --minimal-margins -C project .
```

PDF creation also measures the selected font's widest safe glyph and clamps the
payload width to whichever is smaller: what fits between the margins at the
requested size/spacing, or 60 characters -- the one width every OCR-safety
measurement in this project (including the OCR-B "dense" preset) was actually
taken at. A font whose glyphs are narrow enough to geometrically fit more than
60 characters does **not** automatically get a wider row: real-content testing
found a wider row (e.g. OCR-B 6pt's own ~90-char geometric fit) measurably less
reliable than 60, even for a font otherwise considered OCR-safe. Text and Word
output retain 60 by default for the same reason, on top of not exposing
reliable physical font metrics.

`--line-width` accepts three spellings:

- `auto` (the default) — the OCR-measured-safe capacity (≤ 60). Same as
  omitting the flag.
- `max` — the largest row that *physically* fits the font/size/margins. This
  may exceed 60 and is **not** OCR-verified; choosing it is the explicit opt-in
  past the safe cap. On text/Word output (no physical font metrics) `max` is an
  error — use `auto` or an integer.
- an integer ≥ 2 — an explicit column count. An integer **above** the safe cap
  needs `--force` (and must still fit the geometric width); below the cap it is
  accepted directly. On formats without font metrics no cap is enforced.

A wider-than-60 row (`max`, or a forced integer) hasn't passed the OCR
print/rasterize/restore benchmark and may reduce restore reliability — the
render-time guard still fails loud if a frame physically overflows the page.
Decode infers row width from the frames, so no separate restore option is
needed.

Create uses disk-backed temporary spools so archive-sized payloads and encoded
page lists do not have to remain in memory. Spools use the system temporary
directory by default; advanced users can select a same-filesystem location with
`--temp-dir PATH` and tune sequential I/O with `--chunk-size BYTES` (default:
1 MiB). Temporary files are removed on both success and failure. DOCX generation
still retains python-docx's in-memory document model, while text and PDF output
are written directly to their destinations.

PDF and DOCX output can center each fixed-width line with
`--horizontal-alignment center`, or distribute its characters across the full
printable width with `--horizontal-alignment justify`. Use
`--character-spacing POINTS` for a smaller, fixed amount of extra tracking. For
example:

```console
glyphive create -f spaced.pdf --font ocr-b --font-size 8 \
  --horizontal-alignment center --character-spacing 0.2 -C project .
```

These controls change physical placement. For PDF, character spacing can also
reduce the automatically selected payload width; use `--line-width` when an
experiment requires identical wire rows across layout variants. `justify` uses
different tracking on lines of different lengths, so
`center` plus modest fixed spacing is usually the better starting point for a
character-grid OCR workflow. Treat either option as experimental until it has
passed the same print/rasterize/OCR benchmark as the chosen font and size.

Text output preserves exact line endings and separates pages with a form-feed
character. Do not reflow or word-wrap a transcript.

## Compression

| Name | Dependency | Notes |
| --- | --- | --- |
| `none` | none | Useful for isolating encoding and OCR behavior |
| `gzip` | none | Portable stdlib compression |
| `zstd` | `glyphive[zstd]` | Optional whole-stream compression |

The legacy `--none`, `-z`/`--gzip`, and `--zstd` flags remain aliases. They are
mutually exclusive, and a legacy flag that disagrees with `--compression`
causes the command to stop before writing output.

`-L`/`--level` forwards a compression level to the selected implementation.

## Redundancy and whole-page recovery

Two independent layers protect a printed document, tuned separately:

- **Per-line Reed-Solomon** (`--parity-ratio FLOAT`, default `0.12`) heals
  scattered OCR character errors on the pages you *do* recover. Lower values
  shrink the page count but leave less correction budget. `--simple` is a
  documented low-redundancy preset (`0.04`) for small, disposable, or easily
  re-typeable documents.
- **Whole-page parity** (`--parity-pages K`, default `0`) emits K extra pages
  of document-level Reed-Solomon parity over the data pages, so the document
  survives up to K *wholly lost* pages (physically destroyed, unscannable, or
  dropped) — not just character noise. Costs K extra printed pages. Data pages
  plus K must not exceed 255 (a create-time error names the cap).

```bash
glyphive create -f resilient.pdf --parity-pages 2 -C project .
```

Even with `--parity-pages 0`, a missing page is no longer an immediate failure:
restore lets the per-line Reed-Solomon try to recover it from the surviving
pages, and only fails if that budget is exceeded. Dedicated parity pages make
that recovery guaranteed up to K lost pages regardless of the per-line budget.

## Metadata profiles

- `--metadata none` is the default. It archives paths, file contents, and
  explicitly empty directories.
- `--metadata basic` also records ordinary permission bits and modification
  time rounded to integer milliseconds.

Metadata restoration is best-effort because filesystems and operating systems
do not all expose the same permissions or time resolution.

## Header line

Page 1 begins with a compact, human-readable summary:

```text
#!glyphive v1 base16c-crc16-rs,zstd files=25 bytes=211233 pages=61
```

This line is **display-only** — restore reads every authoritative value from the
CRC-protected `H` frames, never from this prose (see the
[wire format](wire-format.md)). It deliberately omits the SHA-256 and metadata
profile, and any line starting with `#!` is treated as a comment on the read
path. Pass `--no-header` to omit the line entirely for the tightest possible
page; the document still restores identically because nothing depends on it:

```bash
glyphive create -f tight.txt --no-header -C project .
```

## Ignore behavior

Glyphive reads `.gitignore` and `.ignore` only at the archived root. Matching
files and directories are excluded, and `.git/` is always excluded. Nested
ignore files are not currently applied.

Use `--no-ignore` to archive everything except `.git/`:

```bash
glyphive create -f complete.txt --no-ignore --compression gzip -C project .
```

Symbolic links, junctions, and special files are rejected rather than followed;
the current archive format has no link or device record.

## Verify the result

Inspect the protected header and file manifest before printing:

```bash
glyphive list -f backup.txt
```

For important backups, perform a full restore into a new directory and compare
it with the source before treating the printout as the only copy.
