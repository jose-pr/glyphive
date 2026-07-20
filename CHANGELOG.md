# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **Decode hardening (rescues documents that previously hard-failed).** A
  CRC-failed line whose index token was misread used to be trusted for stream
  geometry, so one bad character could claim an impossible index (~900,000 vs a
  true max ~1,000) and make decode die with "cannot recover RS parameters" even
  with the parity budget barely touched — every ~30 KB document failed at 0.1 %
  character error. Decode now (1) attempts a **CRC-guided single-substitution
  repair** of each failed line (accepted only when exactly one candidate
  reproduces the printed CRC — the CRC is the oracle, never decompressibility),
  (2) computes stream geometry **only from CRC-valid lines** and positionally
  reassigns or drops implausible-index failed lines, and (3) **degrades a
  conflicting-duplicate collision to an erasure** (which Reed-Solomon rebuilds)
  instead of aborting the whole decode. Applies to all existing documents; no
  bytes on paper change, and the clean-transcript fast path is untouched.

## [0.1.0] - 2026-07-18

The first public release provides an end-to-end path from a file tree to
OCR-friendly printable pages and back to a verified tree.

### Changed

- **Compact `#!glyphive` header line**: the display-only summary is now
  `#!glyphive v<N> <codec>[,<comp>] files=.. bytes=.. pages=..` — a bare `v<N>`
  token, codec and compression collapsed to one positional `codec[,comp]` token,
  and `sha256`/`meta` dropped entirely (they live in the protected `H` frames).
  Fewer characters on page 1 means less to OCR and less display-line overflow.
  Any line beginning with `#!` is now treated as a comment on the read path, so
  documents can carry arbitrary `#!` notes. (Pre-release format change; no
  backward compatibility with the old header grammar.)
- **`--descan auto` retry now sweeps a `0.6`/`0.8` blur ladder** (was a single
  `0.6` pass): the widest glyphs (e.g. Courier 12 pt) can need `~0.8` to decode
  from a raw photo, so the auto-retry covers both. The per-line CRC merge keeps
  this strictly additive — extra blur passes only ever recover more lines.
- **Default PDF font is now `dejavu-sans-mono`** (was Courier). DejaVu was one of
  only two fonts that held up on real photographed scans under `tesseract-glyphive`
  and passes the byte-for-byte restore gate, so it is preferred for recovery
  robustness. Trade-offs, accepted deliberately: it renders ~26% fewer usable
  bytes/page than Courier at 8 pt+ (more pages per document) and embeds ~340 KB per
  PDF (Courier is a zero-embedding core font). Pass `--font courier` for the denser,
  zero-embed alternative. The synthetic density comparison and the override rationale
  are in `benchmarks/results/FONT_CANDIDATES.md`.
- **Footer-hash mismatches are now advisory, not warnings**: an OCR restore
  almost always produces a footer-hash mismatch (OCR inserts interior spaces
  that change the page text hash while the `L`/`P` lines still decode via
  CRC/RS), so logging it at WARNING cried wolf on every clean restore. These are
  now collected separately (`meta["_footer_hash_notes"]`) and logged at INFO
  (shown with `-v`); genuine page-integrity issues (missing/reconstructed pages)
  stay at WARNING. `glyphive inspect` reports the advisory count. Correctness is
  unchanged — it never rested on the page footer hash.
- **Faster decode on clean input**: decode now skips Reed-Solomon entirely on
  erasure-free blocks (the common case — text transcripts and good scans are
  mostly clean), since a line whose per-line CRC matched is already trusted and
  RS has nothing to correct. A partially-damaged stream RS-corrects only the
  blocks that actually contain a bad/missing line. Correctness is unchanged: the
  per-line CRC oracle plus the whole-document SHA-256 gate still catch any
  corruption loudly. (Profiling put RS at ~75% of clean-decode time; the exact
  speedup is measured in CI, not stated here.)
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

- **`create --no-header`**: omit the display-only `#!glyphive` summary line from
  page 1 for the tightest possible page. Restore needs nothing from it (all
  authoritative metadata comes from the CRC-protected `H` frames), so a
  `--no-header` document restores byte-for-byte identically.
- **Bundled `dejavu-sans-mono` PDF font**: DejaVu Sans Mono 2.37 (permissive
  DejaVu Fonts License) is now a selectable bundled font
  (`--font dejavu-sans-mono`), embedded in PDF output. It was one of only two
  fonts (with Courier) that held up under the `tesseract-glyphive` profile in
  real scan/restore testing — offered as a strong option; a byte-for-byte
  restore gate is pending before it is recommended over Courier. Adds ~335 KB
  to the wheel. (Also fixes a latent bug where a second bundled font would have
  collided on the hardcoded `"OCR-B"` FPDF family name.)
- **`glyphive inspect` — recovery-headroom report**: a read-only subcommand
  that reports how much damage a document can survive without fully decoding or
  verifying it (so it works on a partially damaged scan a restore would reject,
  and writes nothing). Shows data/parity page counts (whole-page recovery
  budget), the realized per-line Reed-Solomon `nsym` (scattered-damage budget),
  and which pages are present/missing/reconstructable. `--json` for machine
  output; `--strict` exits non-zero on an already-unrecoverable document. Backed
  by a new pure `codec.base16c.describe_line_stream` shape oracle.
- **`--line-width auto|max|<int>` spellings** on `create`: `auto` (default) is
  the OCR-measured-safe capacity (≤60); `max` fills the largest row that
  physically fits the font/size/margins (may exceed 60, not OCR-verified, and
  an error on formats without font metrics); an integer above the safe cap now
  requires `--force`. The renderer interface gains a public
  `geometric_payload_capacity` hook (uncapped fit) alongside the safety-capped
  `payload_capacity`.
- **De-scan blur for photographed input (`--descan`, auto by default)**:
  `extract`/`list` apply a Gaussian blur to image and rasterized-PDF input
  before OCR. The default `--descan auto` does a single sharp pass, then
  automatically retries once with a light `0.6` blur if that fails to decode
  image/PDF input — raw phone photos are often too sharp/noisy for the frame
  CRC/RS and otherwise fail, and the retry costs an extra OCR pass only on
  failure. `--descan 0` disables the auto-retry; an explicit list
  (`--descan 0,0.6,1.0`) OCRs each image at every radius and **merges the
  CRC-valid lines across passes** — different blurs recover different lines and
  the per-line CRC makes combining them safe, so a document no single blur can
  fully read may still restore from the union. Automatic OCR-engine selection
  also prefers the constrained `tesseract-glyphive` profile over plain
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

[Unreleased]: https://github.com/jose-pr/glyphive/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jose-pr/glyphive/releases/tag/v0.1.0
