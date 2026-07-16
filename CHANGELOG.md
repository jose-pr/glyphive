# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning once it reaches 1.0.

## [0.1.0] — Unreleased

First working end-to-end release: archive a tree to OCR-friendly printable pages
and restore it byte-for-byte.

### Added
- **Standalone pyz packaging** (`package.py`): releases now include a
  universal `glyphive.pyz` with the required runtime dependencies and the core
  text/none/gzip feature scope, plus explicitly named OS-specific `[all]` pyzs
  for optional integrations. Lightweight OCR Python shims (`Pillow`,
  `pytesseract`) are optional; heavyweight OCR engines/models remain external
  and are not bundled.
- **Codec registry** (`codec/`): the `g1` implementation now has a typed named
  lookup; callers select the registered implementation directly.
- **Compression registry** (`compression/`): `none`, `gzip`, and lazy optional
  `zstd` methods now use the same named lookup contract.
- **Renderer/OCR registries** (`render/`, `restore/ocr/`): text, PDF, Word, and
  optional OCR providers use explicit registries with lazy backend imports.
- **Codec `g1`** (`codec/`): the measured-safe `ABCDHKLMPRTVXY34` alphabet
  (16 symbols, 4-bit/nibble packing), a per-line CRC-16 check (4 safe chars),
  masked 5-character indices, and document-wide interleaved Reed-Solomon parity.
  The alphabet was selected from Courier 8pt / 300 DPI / Tesseract 5.4.0
  measurements; it uses case-folding only and no confusable aliases. Scattered
  OCR errors self-heal; correctness is judged only by CRC/RS, never a "did more
  bytes decompress" proxy. No decode repair-search.
- **Archive** (`archive.py`): binary-safe length-prefixed record stream (magic
  `GLYPHIV1`) for arbitrary bytes, deterministic ordering, `.gitignore`/`.ignore`
  filtering via `pathspec` (root-level), and `none`/`gzip`/`zstd` compression.
- **Layout** (`layout.py`): authoritative document metadata now uses
  safe-alphabet, CRC-protected `H` header frames and `T` page-footer frames.
  Fixed-width index/check fields, bounded payloads, envelope length/digest checks,
  and protected page hashes make damaged metadata fail loud. The unrestricted
  `#!glyphive` summary and trailing `PAGE n/total` prose are display-only.
- **Renderers** (`render/`): plain text, PDF (fpdf2), and Word (python-docx), each
  with selectable font family + size (OCR-tuned monospace defaults). PDF currently
  uses FPDF core fonts only; DOCX accepts arbitrary installed Word font names.
- **Restore** (`restore/`): text-transcript decode with whole-document SHA-256
  verification, path-traversal-safe unarchive, and a thin optional multi-engine
  OCR orchestration layer (Paddle/EasyOCR/Tesseract) for restoring from images.
- **CLI** (`cli/`): tar/bsdtar-like `create`/`extract`/`list` on `duho`, split
  into command modules with codec, compression, renderer, metadata, and OCR
  selectors resolved through the named registries.
- **Verification**: 91 tests pass, and real Tesseract 5.4.0 Courier 8pt / 300 DPI
  PDF-to-image gates restore both `none` and `zstd` fixture trees byte-for-byte.

### Known limitations
- `create` archives a single path (a directory or `.`) in v1.
- Ignore files are honored at the archived-tree root only (nested `.gitignore`
  is not yet applied).
- A fully lost/unscannable page is reported (fail-loud), not reconstructed;
  whole-page recovery via parity pages is planned.
- `H`/`T` metadata CRCs provide integrity detection, not correction. Metadata
  needs Reed-Solomon protection or independently validated duplication before
  the first release.
- QR-code output is planned, not yet implemented.
