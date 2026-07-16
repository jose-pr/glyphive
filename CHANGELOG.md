# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

The first public release provides an end-to-end path from a file tree to
OCR-friendly printable pages and back to a verified tree.

### Added

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
  with codec, compression, metadata, renderer, and OCR selectors.
- **Documentation and examples**: task-focused create, restore, wire-format,
  OCR, benchmark, and API pages plus a runnable create/restore example.

### Changed

- Require `pathlib_next>=0.8.1`, including its Python 3.9 import and local path
  walking fixes.

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
