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
  --codec base16g-crc16-rs \
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
PaddleOCR with the project's `base16g-crc16-rs` alphabet — see
[`benchmarks/results/FONT_CANDIDATES.md`](https://github.com/jose-pr/glyphive/blob/master/benchmarks/results/FONT_CANDIDATES.md)
for the full matrix. Any other font must still be measured with the intended
OCR model before relying on it: a smaller font fits more characters on a
page, but it must be validated on the intended printer, scanner, resolution,
and OCR engine — nominal density is not the same as recoverable density.

### Small fonts are a large density lever

Font size scales chars-per-page by roughly `(8/size)**2` (both page dimensions
shrink), so a smaller font is a bigger density lever than a wider alphabet. What
matters is not the raw OCR character-error rate but whether the whole page still
**restores byte-for-byte** — the per-line CRC + Reed-Solomon correct a surprising
amount of small-font OCR noise before decode fails.

End-to-end restore was validated (real `create` → 300 DPI render → stock
Tesseract with the base16g whitelist → `extract` → byte-diff) on the default
`base16g` codec:

| size | density vs 8pt | restores byte-identical (stock OCR)? |
|------|---------------:|--------------------------------------|
| 8pt  | 1.0x | ✅ yes |
| 5pt  | 2.6x | ✅ yes |
| 4pt  | 4.0x | ✅ yes (clean and Gaussian-blurred) |
| 3pt  | ~7x  | ✅ yes (OCR-B; monospace fonts marginal at 3pt) |

So **`--font-size 4` roughly quadruples density and still restores with plain
Tesseract — no trained model required** (the format's error correction does the
work). Caveats: validate on your actual printer/scanner/DPI/engine (nominal
density is not recoverable density), and pick OCR-B for the smallest sizes.

> **Note on trained OCR models: none are needed.** Byte-restore-gated
> evaluation (2026-07-19 through 2026-07-22) settled what a trained model buys
> over stock Tesseract, and the answer is nothing:
> - `base16g` restores byte-for-byte on stock down to 4pt and across row widths.
> - `base32g` **also restores on stock** as of the current format — earlier
>   measurements that showed it needing a model predate the decode hardening,
>   machine-frame Reed-Solomon and final-line padding fixes, which is what moved
>   it from model-required to stock-viable. See its font limits below.
> - `base64`/`base64g` cannot be rescued by a model at all: no conflict-free
>   64-glyph set exists in printable ASCII (the maximum mutually-distinct set is
>   55, 52 usable), so their alphabets must double-book OCR-confusable classes.
>   They stay encode-only.
>
> Every model this project trained — on synthetic lines, on framed pages, and on
> page rasters — either matched stock or lost to it. The published
> `glyphive-ocrmodel-*` packages were trained on the wrong data and should not be
> relied on. Stock `tesseract-glyphive` is the recommended engine for every
> codec.

### Choosing a denser codec: `base32g` is Courier-only

`base32g-crc16-rs` carries 5 bits per character instead of 4, so it is ~25%
denser than the default. On stock OCR that density is **font-dependent**, and
the dependency is sharp rather than gradual (measured 2026-07-22, 3 fonts ×
5 sizes × 2 widths, byte-restore gated):

| font | 4pt | 5pt | 6pt | 8pt | 10pt |
|------|-----|-----|-----|-----|------|
| Courier | ✅ | ✅ | ✅ | ✅ | ✅ |
| OCR-B | ❌ | ❌ | ❌ | ❌ | — |
| DejaVu Sans Mono | ❌ | ❌ | ❌ | ❌ | ❌ |

(width 60; Courier also restores at width 90 from 5pt up. `—` is a geometry
refusal, not an OCR failure.)

The cause is the alphabet: `base32g` adds `?@!&+=` to the base16g set, and those
punctuation glyphs are the ones OCR drops or mangles on OCR-B and DejaVu. A
dropped glyph shortens the line, which desynchronizes the frame parse — a
failure Reed-Solomon cannot repair, unlike an ordinary misread character.

**So: use `base32g` only with Courier.** For any other font, `base16g` is the
correct choice; a smaller font size buys far more density than a wider alphabet
anyway (4pt is ~4× denser than 8pt and still restores on stock).

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
60 characters does **not** automatically get a wider row: the raw per-character
OCR error rate climbs as rows get denser (measured, e.g., on OCR-B 6pt's own
~90-char geometric fit), so 60 is the width that is safe across every font and
size. A wider row can still restore byte-for-byte once the frame CRC + RS
correct that extra noise (see the `--force` note below), but that margin is
font/size-specific, not guaranteed — which is why the conservative width is the
default rather than the maximum. Text and Word output retain 60 by default for
the same reason, on top of not exposing reliable physical font metrics.

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

A wider-than-60 row (`max`, or a forced integer) is past the conservative
default cap, but it is **not** automatically unsafe. A byte-restore benchmark
(create → 300 DPI raster → OCR → `extract` → byte-diff) over Courier `base16g`
restored byte-for-byte at row widths 60, 75, and **90 (50% past the cap)** at
both 8pt and 5pt, clean and lightly blurred — the frame CRC + Reed-Solomon
absorb the extra OCR noise a denser row adds. The `60` default stays because it
is the width every OCR-safety measurement was taken at and the safe width across
*all* fonts/sizes; wider rows are font- and size-specific and the render-time
guard still fails loud if a frame physically overflows the page (at 8pt Courier
the geometric fit is ~98 characters, and `create` refuses anything past it).
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

Three independent layers protect a printed document, tuned separately:

- **In-line parity** (`--line-parity {0,2,4}`, default `2`) puts a small
  Reed-Solomon field on each printed line, covering that line's own index and
  payload. A line with one or two bad characters is corrected **in place** —
  re-rendered and re-checked against its own CRC — so it never spends any of the
  document-level budget below. This is the cheap insurance that matters for aged
  or lightly damaged paper: `2` costs about 6.9 % more printed characters, `4`
  about 12.3 %, and `0` restores the classic three-token frame with no overhead.
  Measured effect at the default: documents survive roughly a 0.5 % character
  error rate where they previously failed below 0.1 %.
- **Document-wide Reed-Solomon** (`--parity-ratio FLOAT`, default `0.12`) heals
  what the in-line layer could not — a line too damaged to correct becomes a
  known erasure and is rebuilt from parity carried on other lines. Lower values
  shrink the page count but leave less correction budget. `--simple` is a
  documented low-redundancy preset (`0.04`) for small, disposable, or easily
  re-typeable documents.
- **Whole-page parity** (`--parity-pages K`, default `0`) emits K extra pages
  of document-level Reed-Solomon parity over the data pages, so the document
  survives up to K *wholly lost* pages (physically destroyed, unscannable, or
  dropped) — not just character noise. Costs K extra printed pages. Data pages
  plus K must not exceed 65,535 (a create-time error names the cap). Documents
  of 255 blocks or fewer use Reed-Solomon over GF(2^8); larger ones switch
  automatically to GF(2^16), which pairs adjacent bytes into 16-bit symbols so
  the codeword limit rises from 255 to 65,535. The choice is recorded in the
  protected header, so restore selects the right field without being told, and
  `glyphive inspect` reports which one a document uses. Encoding a 1,000-page
  document with `--parity-pages 8` measures ~2.7 s.

```bash
glyphive create -f resilient.pdf --parity-pages 2 -C project .

# Maximum in-line correction (widest per-line parity), for paper you expect to age
glyphive create -f archival.pdf --line-parity 4 --parity-pages 2 -C project .

# No per-line parity — smallest page count, relies on the document-wide layer
glyphive create -f compact.pdf --line-parity 0 -C project .
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
#!glyphive v1 base16g-crc16-rs,zstd files=25 bytes=211233 pages=61
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
