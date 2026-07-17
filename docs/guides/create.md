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
  --codec g1 \
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
`symbol`, `zapfdingbats`, and `arial`, the bundled `ocr-b` option, or an
existing `.ttf`/`.otf` path. Word output accepts an installed Word font name.
OCR-B is bundled under the SIL Open Font License 1.1 and is not yet the default:
measure it with the intended OCR model before relying on it. A smaller font fits
more characters on a page, but it must be
validated on the intended printer, scanner, resolution, and OCR engine; nominal
density is not the same as recoverable density.

The number of rows per page is calculated from the selected font size and page
geometry. Use `--minimal-margins` to reduce all margins from 36 points to 12
points and use more of the sheet. Confirm that the resulting printable area is
inside the limits of the intended printer and scanner before relying on it:

```bash
glyphive create -f dense.pdf --format pdf --font-size 8 --minimal-margins -C project .
```

PDF and DOCX output can center each fixed-width line with
`--horizontal-alignment center`, or distribute its characters across the full
printable width with `--horizontal-alignment justify`. Use
`--character-spacing POINTS` for a smaller, fixed amount of extra tracking. For
example:

```console
glyphive create -f spaced.pdf --font ocr-b --font-size 8 \
  --horizontal-alignment center --character-spacing 0.2 -C project .
```

These controls change physical placement only; they do not change the encoded
transcript. `justify` uses different tracking on lines of different lengths, so
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

## Metadata profiles

- `--metadata none` is the default. It archives paths, file contents, and
  explicitly empty directories.
- `--metadata basic` also records ordinary permission bits and modification
  time rounded to integer milliseconds.

Metadata restoration is best-effort because filesystems and operating systems
do not all expose the same permissions or time resolution.

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
