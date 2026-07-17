# glyphive

[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](CHANGELOG.md)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/glyphive/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/glyphive/test.yml)](https://github.com/jose-pr/glyphive/actions/workflows/test.yml)

Archive a file tree to **compact, OCR-friendly, printable pages** and restore
it from a transcript or scan. Glyphive keeps the paper representation
human-readable while using checksums and error correction to detect or repair
the character errors that make ordinary base64-on-paper fragile.

> **Status: alpha.** The wire format and CLI may change before 1.0.

## Features

- **Printable text, PDF, or Word output** with selectable font family and size.
- **Bundled OCR-B PDF option** under the SIL Open Font License, plus custom
  `.ttf`/`.otf` PDF font paths for measured channels.
- **Measured OCR-safe `base16c-crc16-rs` alphabet** (`ABCDHKLMPRTVXY34`) with no confusable
  character aliases.
- **Localized integrity checks** on every encoded line and protected page
  metadata.
- **Document-wide Reed-Solomon parity** for correcting scattered OCR errors.
- **Binary-safe archives** for nested trees, empty directories, and arbitrary
  file contents.
- **Deterministic restore** from text; optional OCR providers are loaded only
  when image input is requested.

## Installation

```bash
pip install glyphive
```

Optional features:

| Extra | Adds | Needed for |
| --- | --- | --- |
| `pdf` | `fpdf2` | PDF output |
| `docx` | `python-docx` | Word (`.docx`) output |
| `zstd` | `zstandard` | zstd compression |
| `ocr` | `Pillow`, `pytesseract` | Tesseract image bridge; the Tesseract program is installed separately |
| `qr` | `segno`, `zxing-cpp`, `Pillow` | QR envelope generation and image decoding without OpenCV |
| `all` | all packages above | All lightweight integrations |

Glyphive requires `pathlib_next>=0.8.1` and Python 3.9 or newer.

## Quick start

Create a text archive from one directory, inspect it, and restore it:

```bash
glyphive create -f backup.txt --compression gzip -C project .
glyphive list -f backup.txt
glyphive extract -f backup.txt -C restored
```

Restore or inspect an already-generated GQ1 QR image set explicitly with
`glyphive extract -f qr-pages/ --from-qr -C restored` or
`glyphive list -f qr-pages/ --from-qr`. Ordinary image input continues through
OCR; QR mode requires `glyphive[qr]` and rejects mixed, duplicate, corrupt, or
incomplete symbol sets before writing files.

Create QR-only or hybrid human-readable/QR PDF pages with an explicit format:

```bash
glyphive create -f backup.pdf --format qr -C project .
glyphive create -f backup-hybrid.pdf --format hybrid -C project .
```

A `.pdf` filename without `--format` remains the ordinary text PDF renderer;
the suffix cannot distinguish PDF, QR, and hybrid presentations.

Tar-style mode flags are equivalent when a positional command is inconvenient:

```bash
glyphive -c -f backup.txt -C project .
glyphive -t -f backup.txt
glyphive -x -f backup.txt -C restored
```

### Installed plugins

Glyphive can explicitly discover implementations supplied by installed Python
distributions. Pass the global `--plugins` flag to opt in for that invocation:

```bash
glyphive --plugins create -f backup.txt --codec vendor_codec -C project .
```

Plugin distributions register a concrete typed class under one of these entry
point groups:

| Entry-point group | Required base class |
| --- | --- |
| `glyphive.codecs` | `glyphive.codec.Codec` |
| `glyphive.compression` | `glyphive.compression.CompressionMethod` |
| `glyphive.render_formats` | `glyphive.render.RenderFormat` |
| `glyphive.ocr_providers` | `glyphive.restore.ocr.OcrProvider` |

The entry-point name must exactly match the class's lowercase registry `name`.
Library callers use `glyphive.plugins.discover()`; normal imports and registry
lookups never discover plugins. Discovery is deterministic and cached, and a
bad candidate is reported without preventing valid candidates from loading.

Installed plugin code executes with the same permissions as Glyphive and is
not sandboxed. Use `--plugins` only when you trust every installed distribution
that declares one of these groups. Discovery does not download or update code.

Create a PDF instead:

```bash
pip install "glyphive[pdf,zstd]"
glyphive create -f backup.pdf --compression zstd -C project .
```

Creation uses bounded-memory disk spools; `--temp-dir` selects their location
and `--chunk-size` tunes sequential I/O for unusually constrained systems.

Restore scans or generated documents with Tesseract. Input type is detected
from file contents first and the extension second:

```bash
pip install "glyphive[ocr,document-input]"
glyphive extract -f scans/ --ocr-engine tesseract -C restored
glyphive list -f backup.pdf --ocr-engine tesseract
```

Restore verifies global integrity before publishing staged files. Advanced
resource controls include `--temp-dir`, `--chunk-size`, and
`--max-output-bytes` on both `extract` and `list`. Transcript frames,
compressed payload, decompressed archive, and staged files are disk-backed;
Reed-Solomon correction retains only compact offsets and one codeword at a time.

The operating-system Tesseract executable and language data must also be
installed. PDF input uses `pypdfium2`; Glyphive-generated DOCX transcripts are
read directly with `python-docx`, without Microsoft Word or LibreOffice. See the
[create guide](https://jose-pr.github.io/glyphive/guides/create/),
[restore guide](https://jose-pr.github.io/glyphive/guides/restore/), and
[OCR guide](https://jose-pr.github.io/glyphive/guides/ocr/) for details.

## Format at a glance

The default `base16c-crc16-rs` format uses exactly 16 payload symbols, or 4 bits per printed
character. Each data (`L`) or parity (`P`) line has a masked index and a full
CRC-16 encoded in the same safe alphabet:

```text
L<5 safe index chars> <up to 60 safe payload chars> #<4 safe CRC chars>
```

CRC-protected `H` header frames carry the codec, compression method, page
count, and whole-document SHA-256. Each page has a protected `T` footer with
its page number and a truncated page hash. Human-facing `#!glyphive` and
`PAGE n/total` text is display-only; restore trusts the protected frames.

Small, scattered line errors become known erasures and can be repaired by the
document-wide Reed-Solomon parity. A missing page is reported rather than
guessed. Read the [wire-format guide](https://jose-pr.github.io/glyphive/guides/wire-format/)
for the complete layering and framing rules.

## Standalone zipapp

Releases may include a self-contained `glyphive.pyz`. Build the universal core
artifact with:

```bash
python package.py --out dist/glyphive.pyz
python dist/glyphive.pyz --help
```

The universal zipapp includes text output with `none` and `gzip` compression.
Optional, platform-specific artifacts can be built with `--extras`; use
`python package.py --help` for the declared choices.

## API overview

| Module | Purpose |
| --- | --- |
| `glyphive.archive` | Serialize and inspect deterministic archive streams, including chunked reader/writer primitives |
| `glyphive.codec` | Resolve printable codecs; includes `base16c-crc16-rs` |
| `glyphive.compression` | Resolve `none`, `gzip`, and optional `zstd` compression |
| `glyphive.layout` | Paginate frames and verify protected page metadata |
| `glyphive.plugins` | Explicitly discover trusted installed entry points |
| `glyphive.render` | Render pages as text, PDF, or Word |
| `glyphive.restore` | Decode documents and safely restore file trees |
| `glyphive.restore.ocr` | Select OCR providers and OCR page images |

Full generated API documentation is available on the
[documentation site](https://jose-pr.github.io/glyphive/api/overview/).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, test, documentation, and pull
request guidance.

Use a project-local virtual environment, then run the lightweight suite:

```bash
python -m venv .venv/dev
.venv/dev/Scripts/python -m pip install -e ".[all,dev,docs]"
.venv/dev/Scripts/python -m pytest -q
.venv/dev/Scripts/python -m mkdocs build --strict
```

On POSIX, replace `.venv/dev/Scripts/python` with
`.venv/dev/bin/python`. OCR sweeps and performance measurements are separate
manual workloads; see [Benchmarks](https://jose-pr.github.io/glyphive/benchmarks/).

### Releasing

Glyphive follows [Semantic Versioning](https://semver.org/) and keeps a
[`CHANGELOG.md`](CHANGELOG.md). Pushing a `v*` tag runs the release validation,
package build, publication, and documentation deployment workflow.

## License

MIT — see [LICENSE](LICENSE).

The bundled OCR-B font retains its SIL Open Font License 1.1; see
[third-party licenses](THIRD_PARTY_LICENSES.md).
