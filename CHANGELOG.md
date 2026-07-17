# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

The first public release provides an end-to-end path from a file tree to
OCR-friendly printable pages and back to a verified tree.

### Added

- **Constrained Tesseract profile** (`tesseract-glyphive`): an opt-in OCR
  provider using PSM 6, Glyphive's exact machine alphabet, and disabled general
  language dictionaries. The existing `tesseract` provider remains unchanged.
- **Bounded archive and compression primitives**: archive records can now be
  written and parsed as fixed-size chunks, and the built-in none/gzip/zstd
  methods support binary stream adapters. Existing one-shot APIs remain
  available while create/restore migrate to disk-backed spools. Create now
  streams archive, compression, FEC, pagination, and renderer output through
  temporary spools; `--temp-dir` and `--chunk-size` control spool placement and
  I/O granularity.
- **Explicit plugin discovery** (`glyphive.plugins`): trusted installed
  distributions can provide typed codecs, compression methods, render formats,
  or OCR providers through four documented entry-point groups. Discovery is
  opt-in through the library API or global `--plugins` CLI flag, deterministic,
  cached, and reports broken candidates without changing normal imports.
- **Standalone zipapp packaging** (`package.py`): build a universal
  `glyphive.pyz` containing the required runtime dependencies and the core
  text/none/gzip feature set, or explicitly named platform-specific artifacts
  with optional integrations. Lightweight OCR Python shims are optional;
  heavyweight OCR engines and models remain external.
- **Codec registry** (`glyphive.codec`): typed named lookup with the built-in
  `g1` codec.
- **Compression registry** (`glyphive.compression`): named `none`, `gzip`, and
  lazy optional `zstd` methods.
- **Renderer and OCR registries** (`glyphive.render`,
  `glyphive.restore.ocr`): text, PDF, Word, and optional OCR providers use
  explicit registries with lazy backend imports.
- **Codec `g1`**: the measured-safe `ABCDHKLMPRTVXY34` alphabet (16 symbols,
  4-bit packing), a full CRC-16 per line, masked five-character indices, and
  document-wide interleaved Reed-Solomon parity. Scattered OCR errors can
  self-heal; correctness is judged by CRC and parity rather than speculative
  character substitution.
- **Binary-safe archive stream** (`glyphive.archive`): length-prefixed records
  for arbitrary bytes, deterministic ordering, root-level
  `.gitignore`/`.ignore` filtering, empty-directory records, and `none`, `gzip`,
  or `zstd` whole-stream compression.
- **Protected layout metadata** (`glyphive.layout`): safe-alphabet `H` header
  frames and `T` page-footer frames carry authoritative document metadata,
  page identities, and hashes. Unrestricted human-readable summaries are
  display aids only.
- **Text, PDF, and Word renderers** with selectable font family and size. PDF
  output uses built-in FPDF font families; Word output accepts installed Word
  font names.
- **Verified restore pipeline** (`glyphive.restore`): transcript decode,
  whole-document SHA-256 validation, path-traversal-safe extraction, and an
  optional multi-provider OCR layer for image input.
- **Tar-like CLI**: `create` (`c`), `extract` (`x`), and `list` (`t`) commands
  with codec, compression, metadata, renderer, and OCR selectors. Leading
  `-c`, `-x`, and `-t` mode flags work without a positional command.
- **Automatic document input**: `extract` and `list` classify each direct-child
  input independently by magic bytes, extension, then UTF-8 text, so transcript,
  image, PDF, and DOCX pages can be mixed in deterministic order. A conversion
  helper renders PDF pages or diagnostic DOCX transcript pages to PNG with
  configurable DPI and blur; DOCX restore uses `python-docx`, not an office suite.
- **Capacity-aware rendering**: PDF/DOCX row budgets follow font size and page
  margins; `--minimal-margins` uses a compact 12-point profile, and long PDF
  display headers fit rather than clip. Output format is inferred from `-f`
  when `--format` is omitted.
- **Bundled OCR-B PDF font**: `--font ocr-b` embeds a pinned, unmodified
  SIL-OFL-1.1 font; PDF output also accepts explicit `.ttf`/`.otf` paths.
- **Documentation and examples**: task-focused create, restore, wire-format,
  OCR, benchmark, and API pages plus a runnable create/restore example.

### Changed

- Require `pathlib_next>=0.8.1`, including its Python 3.9 import and local path
  walking fixes.
- A directory supplied to CLI `-f` expands its direct child files in stable
  filename order.

### Known limitations

- `create` archives one directory (or `.`) at a time; wrap multiple inputs in a
  directory.
- Ignore files are read at the archived-tree root only; nested `.gitignore`
  files are not applied.
- A fully lost or unscannable page is detected and reported, not reconstructed.
- Protected header/footer metadata detects corruption but is not itself
  Reed-Solomon-corrected.
- QR-code output is not implemented.

[Unreleased]: https://github.com/jose-pr/glyphive/commits/master
