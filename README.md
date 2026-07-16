# glyphive

Archive an arbitrary file tree to a **compact, OCR-friendly, printable** page
format (plain text, PDF, or Word) and restore it from a scan — or from a re-typed
transcript when OCR fails. Think "QR-code paper backup", but the pages stay
human-legible and human-re-typeable, and you are not at the mercy of a phone
camera's QR decoder.

> **Status: alpha.** The format and CLI may still change before 1.0.

## Why not just base64-on-paper?

Because we tried, and recovering a 79-file base64-on-paper backup took three OCR
engines and days of manual pixel-verification — and still stalled. The default
`g1` codec is designed specifically to avoid every failure mode we hit:

- a **measured OCR-safe alphabet** — `ABCDHKLMPRTVXY34`, the exact 16-character
  subset that read back without character errors, inserted lines, or corrupting
  confusions in the Courier 8pt / 300 DPI / Tesseract 5.4.0 measurement; its
  4-bit characters pack bytes as pairs of nibbles, with no confusable aliases;
- a **per-line check** (CRC-16 as 4 safe characters) so a bad line is caught and
  localized *immediately*, without decoding anything downstream;
- **Reed-Solomon parity** so small OCR errors *self-heal* instead of corrupting
  everything after them;
- **CRC-protected `H`/`T` machine metadata** carrying the authoritative document
  header, page numbers, and page hashes; the unrestricted `#!glyphive` and
  `PAGE n/total` prose is only a display aid and is never trusted by restore;
- **no "repair search"** — correctness is judged only by the CRC and RS, never by a
  proxy like "did more bytes decompress", which was the trap that silently
  corrupted human-verified data last time.

## Install

```bash
pip install glyphive            # text output only
pip install "glyphive[pdf]"     # + PDF rendering (fpdf2)
pip install "glyphive[docx]"    # + Word (.docx) rendering (python-docx)
pip install "glyphive[zstd]"    # + zstd compression (denser than gzip)
pip install "glyphive[all]"     # everything, incl. lightweight OCR helpers
```

## Standalone pyz

Releases also include `dist/glyphive.pyz`, a self-contained zipapp that can run
without installing Glyphive first:

```bash
python package.py --out dist/glyphive.pyz
python dist/glyphive.pyz --help
```

The pyz vendors the required runtime dependencies (`duho`, `pathlib_next`,
`pathspec`, and `reedsolo`) and supports the core archive/restore flow with
text output plus `none` or `gzip` compression. Optional PDF, Word, zstd, and
OCR integrations are deliberately not bundled in this universal artifact.

To build an explicit, OS-specific optional artifact, select a declared extra:

```bash
python package.py --extras all --out dist/glyphive-linux-all.pyz
python dist/glyphive-linux-all.pyz --help
```

`--extras` is repeatable and supports the project extras `pdf`, `docx`, `zstd`,
`ocr`, and `all`. Optional pyzs may contain compiled files and must be named for
their target OS. The current `[ocr]` / `[all]` scope includes only lightweight
Python-side OCR helpers (`Pillow`, `pytesseract`), not heavyweight OCR models or
engine binaries. Install any additional external OCR engine separately. The
builder accepts `--out` and `--python`, and honors `SOURCE_DATE_EPOCH` for stable
staging timestamps.

## Usage (tar/bsdtar-like)

```bash
# Create an OCR-friendly archive. Compression defaults to zstd if available,
# else gzip; the .gitignore/.ignore of the archived tree are honored by default.
glyphive create -f backup.pdf --format pdf -C project .

# Plain-text output (directly printable), no compression:
glyphive create -f backup.txt --format text --compression none -C project .

# Explicit registry selections and archive metadata:
glyphive create -f backup.txt --codec g1 --compression gzip --metadata basic .

# Inspect the header + file manifest without extracting:
glyphive list -f backup.txt

# Restore from a re-typed / OCR'd text transcript:
glyphive extract -f backup.txt -C restored

# Restore from a scanned page image with an explicit OCR provider:
glyphive extract -f scan.png --from-images --ocr-engine tesseract -C restored
```

Flags mirror `tar`/`bsdtar`: `-f/--file`, `-C/--directory`, positional paths,
`--codec`, `--compression`, `--metadata none|basic`, `--format`,
`--ocr-engine`, `-L/--level`, `--no-ignore`, `--font`, `--font-size`.
The legacy `-z/--gzip`, `--zstd`, and `--none` selectors remain mutually
exclusive aliases for `--compression`; a generic selector that disagrees with
a legacy alias is rejected before output is written.

PDF output currently supports the built-in FPDF core font families only:
`courier`, `helvetica`, `times`, `symbol`, `zapfdingbats`, and `arial`.
DOCX output accepts arbitrary installed Word font names.

Codec implementations are resolved by their lowercase header identifier through
the in-process registry. Select the built-in implementation with
`glyphive.codec.get("g1")`; v1 documents continue to decode with `g1`.
Compression methods follow the same pattern through `glyphive.compression.get()`;
the `none`, `gzip`, and optional `zstd` names are stable wire identifiers.
Output formats are registered through `glyphive.render.get()` and OCR providers
through `glyphive.restore.ocr.get()`; optional PDF, Word, and OCR dependencies
are loaded only when the selected implementation runs.

**v1 scope note:** `create` archives a single path (a directory or `.`). Wrap
multiple inputs in a directory. QR output and whole-page recovery are planned for
later (see the design notes).

## Format (so a human can inspect a page by hand)

Every document starts with a human-readable summary:

```
#!glyphive v=1 codec=g1 comp=zstd meta=none files=25 bytes=211233 pages=61 sha256=<hex64>
```

The `meta` token records the archive metadata profile. It is `none` by default;
old v1 summaries without the token remain parseable. Restore does not trust this
unrestricted ASCII line. Immediately after it, page 1 carries the authoritative
header as one or more CRC-protected safe-alphabet frames:

```
H<5 safe index chars> <up to 60 safe payload chars> #<4 safe CRC chars>
```

Each page ends with a protected footer followed by display-only prose:

```
T<5 safe index chars> <30 safe payload chars> #<4 safe CRC chars> PAGE 3/61
```

The `H` envelope carries the exact `codec`, `comp`, metadata profile, file and
byte counts, total pages, and document SHA-256. The `T` envelope carries the
0-based page index, total page count, and the first 8 bytes of that page's
SHA-256. The `PAGE 3/61` suffix is for people; changing it does not change what
restore reads.

Every data or parity line is fixed-width and self-checking:

```
L<5 safe index chars> <60 safe payload chars> #<4 safe CRC chars>
```

- `L` = data line, `P` = Reed-Solomon parity line. Each stream has its own
  0-based index, encoded as five safe characters with a fixed per-position mask
  so small indices do not print as repeated glyphs.
- Payload alphabet: exactly `ABCDHKLMPRTVXY34` (16 symbols, 4 bits per character,
  case-insensitive). There are deliberately no `I/l→1`, `O→0`, or other aliases:
  an excluded glyph becomes a detectable erasure for Reed-Solomon correction.
- `#<check4>` = a 16-bit CRC-16/CCITT (poly `0x1021`, init `0xFFFF`) over the
  printed `index+payload` characters, written as 4 safe-alphabet chars — recomputable
  by hand to verify a single line in isolation.

The 16-symbol alphabet costs 25% more payload characters than a 5-bit alphabet,
but avoids the measured silent `Q→O→0` and `J→I→1` corruptions that made
Crockford Base32 unsafe on this print/OCR channel. Courier 8pt at 300 DPI is the
measured validation profile; the renderer remains configurable and the CLI
default is 11pt.

The decoded byte stream is `<compression> ∘ <archive record stream>`; the archive
stream is a length-prefixed binary format (magic `GLYPHIV1`) of
`path/mode/mtime/content` records, so binary files and files containing any
delimiter round-trip safely.

## How the pieces fit

```
create:   tree ──archive.py──▶ bytes ──compress──▶ codec/ (g1 registry) ──▶ lines
                                                          layout.py ──▶ pages ──render/──▶ text/pdf/docx
extract:  text/scan ──[restore/ocr.py]──▶ lines ──layout.read_pages──▶ codec.get(header.codec).decode
                    ──decompress──▶ archive bytes ──restore/unarchive.py──▶ tree
```

Integrity is verified at every level: per-line CRC, per-page hash, and a
whole-document SHA-256 in the header — a restore never silently produces corrupt
output; it fails loud and names what/where.

## Verification

The current suite collects **91 tests**. In addition to unit and in-process
round-trip coverage, real Tesseract 5.4.0 gates render Courier 8pt PDF at 300 DPI,
OCR the resulting page image, and run `glyphive extract --from-images`; both
`none` and `zstd` documents restored the fixture tree byte-for-byte.

## License

MIT — see [LICENSE](LICENSE).
