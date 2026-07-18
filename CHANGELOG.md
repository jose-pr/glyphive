# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

The first public release provides an end-to-end path from a file tree to
OCR-friendly printable pages and back to a verified tree.

### Changed

- **Codec identifier renamed `g1` -> `base16c-crc16-rs`**: exposes the
  composable parts (16-char OCR-safe alphabet / CRC-16 / Reed-Solomon) instead
  of an opaque version tag. `codec/g1.py` -> `codec/base16c.py`; `G1Codec` ->
  `Base16CCodec`; `--codec` default and `codec.names()` updated. Nothing was
  published under the old name, so this is an in-place rename with no
  migration path or dual-format compatibility.
- **Dense preset documented**: the bundled `ocr-b` font (SIL OFL 1.1, OCR-B by
  Raisty) at 6pt measures 5,050 usable bytes/page — 23% denser than the
  Courier 8pt `safe` default — and remains safe on both tested engines
  (Tesseract and PaddleOCR). Select it with `--font ocr-b --font-size 6`; the
  shipped default stays Courier 8pt per the project's font-selection design.

### Fixed

- **PDF frames no longer silently shrink to fit**: a machine/data frame
  (`H`/`L`/`P`/`T`) that overflows the printable width now raises a clear error
  naming the overflow instead of quietly reducing its font size to fit.
  Silent shrinking distorted glyphs (hurting OCR) and hid a misconfigured
  font/size/margin/line-width. The display-only `#!glyphive` human header is
  exempt and still scales to fit (restore never trusts it).
- **Overwrite publication now rolls back on partial failure**: restoring onto
  an existing destination with `--overwrite` moves each existing final file
  aside into a private backup before replacing it. If any staged move fails
  partway through (disk full, permission error, etc.), every file already
  replaced is restored from its backup and any newly created file is removed
  -- the destination is left exactly as it was, never half-migrated.
- **Machine header now Reed-Solomon protected**: the real Tesseract 5.4.0
  end-to-end gate (`create` -> rasterize 300 DPI -> `extract --from-images`
  -> diff) found that `--compression zstd` deterministically failed restore:
  a wider header caused Tesseract to misread one `H` frame's two duplicate
  copies identically, so CRC+duplication alone could not recover it.
  `layout.py` now adds one Reed-Solomon parity chunk over the header
  envelope so restore reconstructs a single damaged chunk instead of only
  detecting it; corruption spanning more than one distinct chunk still fails
  loud. Both `--compression none` and `--compression zstd` now restore
  byte-for-byte through the real OCR gate.
- **Large-document parity overhead**: `parity_ratio` now targets aggregate
  Reed-Solomon parity across all GF(255) blocks. The default is approximately
  12% for large streams instead of repeating a capped whole-stream budget per
  block and inflating parity to roughly 65%.

### Added

- **`--line-width auto|max|<int>` spellings** on `create`: `auto` (default) is
  the OCR-measured-safe capacity (≤60); `max` fills the largest row that
  physically fits the font/size/margins (may exceed 60, not OCR-verified, and
  an error on formats without font metrics); an integer above the safe cap now
  requires `--force`. The renderer interface gains a public
  `geometric_payload_capacity` hook (uncapped fit) alongside the safety-capped
  `payload_capacity`.
- **De-scan blur for photographed input (`--descan`)**: `extract`/`list` can
  apply a Gaussian blur to image and rasterized-PDF input before OCR (default
  0 = off; ~0.6 measured best on real phone photos, which otherwise fail decode
  because they are too sharp/noisy for the frame CRC/RS). Accepts several radii
  (`--descan 0,0.6,1.0`) to OCR each image at every radius and **merge the
  CRC-valid lines across passes** — different blurs recover different lines and
  the per-line CRC makes combining them safe, so a document no single blur can
  fully read may still restore from the union. Automatic OCR-engine selection
  now also prefers the constrained `tesseract-glyphive` profile over plain
  `tesseract` (measured substantially higher scan-restore success).
- **Whole-page recovery (`--parity-pages K`)**: `create` can emit K extra
  document-level Reed-Solomon parity pages over the data pages, so a document
  survives up to K wholly lost / unscannable / destroyed pages — not just the
  scattered per-line OCR errors the per-line RS already fixes. Restore
  reconstructs missing data pages from the parity pages and reports which it
  rebuilt. Default 0 (off, byte-identical to before). Data pages + K must not
  exceed 255. Independent of `--parity-ratio`. The header gains a `pgpar`
  token (omitted when 0) and the protected machine envelope records K and the
  page block size. Separately, a *missing* page no longer hard-fails up front
  even with K=0: the codec's document-wide RS is given a chance to recover it
  from the surviving pages when the per-line parity budget allows.
- **Progress logging for `create`/`extract`**: both commands now log
  intermediate stage events (`archived`, `compressed`, `encoded`, `rendered`
  for `create`; `staged`, `published` for `extract`) instead of only a final
  one-line summary, sparsely rate-limited so a large tree doesn't flood the
  log. The underlying `on_progress` callback is also available to library
  callers of `restore_document_spooled`/`unarchive_spool`.
- **Isolated QR transport primitives**: the optional `qr` extra provides
  deterministic, versioned 1000-byte envelopes, level-H Segno PNG generation,
  and Pillow/ZXing-C++ raw-byte decoding without OpenCV. Extract/list accept
  explicit `--from-qr` image or directory input; ordinary image OCR behavior is
  unchanged. Explicit `--format qr` writes six symbols per Letter page;
  `--format hybrid` writes one symbol plus its transcript slice per page. A
  plain `.pdf` suffix continues to select the ordinary PDF renderer.
- **Constrained Tesseract profile** (`tesseract-glyphive`): an opt-in OCR
  provider using PSM 6, Glyphive's exact machine alphabet, and disabled general
  language dictionaries. The existing `tesseract` provider remains unchanged.
- **Bounded archive and compression primitives**: archive records can now be
  written and parsed as fixed-size chunks, and the built-in none/gzip/zstd
  methods support binary stream adapters. Existing one-shot APIs remain
  available while create/restore migrate to disk-backed spools. Create now
  streams archive, compression, FEC, pagination, and renderer output through
  temporary spools; `--temp-dir` and `--chunk-size` control spool placement and
  I/O granularity. Restore stream-decompresses into a size-limited spool,
  validates the global digest and archive framing, stages files privately, and
  only then publishes final paths; `--max-output-bytes` caps expansion.
  Transcript parsing spools normalized frames in one pass, while
  `base16c-crc16-rs` retains only compact line offsets and processes
  Reed-Solomon codewords blockwise.
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
  `base16c-crc16-rs` codec.
- **Compression registry** (`glyphive.compression`): named `none`, `gzip`, and
  lazy optional `zstd` methods.
- **Renderer and OCR registries** (`glyphive.render`,
  `glyphive.restore.ocr`): text, PDF, Word, and optional OCR providers use
  explicit registries with lazy backend imports.
- **Codec `base16c-crc16-rs`**: the measured-safe `ABCDHKLMPRTVXY34` alphabet
  (16 symbols, 4-bit packing), a full CRC-16 per line, masked five-character
  indices, and document-wide interleaved Reed-Solomon parity. Scattered OCR
  errors can self-heal; correctness is judged by CRC and parity rather than
  speculative character substitution. (Named for its composable parts —
  16-symbol OCR-safe alphabet / CRC-16 / Reed-Solomon — instead of an opaque
  `g1` version tag; renamed before the first release, so no migration shim
  was needed.)
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
