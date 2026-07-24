# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-07-23

Font-robustness and CLI-ergonomics release. A footer-hash bug that had been
silently attributed to "normal OCR noise" for weeks turned out to be a real
canonical-form mismatch; fixing it, plus a first real local (non-VM) font
sweep with a real blur-tolerance stress test, reshaped the default `create`
geometry and retracted a codec recommendation from 0.2.0.

**Breaking (pre-1.0, no compatibility shim): `create`'s default output
geometry changed via a new `--mode` preset system.** `create` now takes
`--mode {conservative,standard,max}`, defaulting to **`standard`**
(omitting `--mode` is the same as passing it): `base16g-crc16-rs`,
`dejavu-sans-mono`, **6pt** (was 11pt), **`--line-width max`** (was the
OCR-measured-safe `auto` cap, ≤60), regular margins. This is a real behavior
change: a bare `create` with no other flags now produces denser, differently
paginated output than before. The new default is a measured choice, not a
guess — it is the most blur-tolerant font/size/width combination found in a
2026-07-23 restore-gate sweep (survives a real Gaussian blur ladder up to
radius 1.5 on both `tesseract` and `tesseract-glyphive`, beating both Courier
and Consolas at the same settings; see
`benchmarks/results/FONT_CANDIDATES.md` "Blur-tolerance stress test"). Any of
`--codec`/`--font`/`--font-size`/`--line-width`/`--minimal-margins` passed
explicitly still overrides just that one field from the mode's preset.
`--mode conservative` (base16g, dejavu-sans-mono, 8pt, `--line-width auto`,
regular margins) reproduces the pre-existing safety-capped behavior aside
from the font-size default; `--mode max` is `standard`'s codec/font/size/width
with `--minimal-margins` for the smallest page count. See the
[create guide](docs/guides/create.md#--mode-measured-codecfontsizewidthmargin-presets)
for the full preset table and rationale.

**Breaking (pre-1.0, no compatibility shim):** `extract --from-images` is
removed. It added no capability over the default (no-flag) path, which
already auto-detects images/PDF/DOCX/text by magic bytes and extension, and
only existed as a narrower, crash-prone alias. Drop the flag; `-f` alone
already does the right thing for image input.

**Guidance retraction: `base32g` is no longer recommended, pending
re-verification.** 0.2.0's "Highlights" claimed `base32g` needs no trained
OCR model and named it Courier-only-but-viable, based on a 2026-07-22 VM
sweep run on Tesseract 4.1.1. A 2026-07-23 local re-gate on Tesseract 5.4.0
found Courier itself — the only font that measurement confirmed — failing to
restore `base32g` at 8pt and 10pt/width-60, the exact cells the 0.2.0
measurement recorded as passing; a `base16g` sanity control on the identical
setup restored fine, ruling out a broken test. As of this writing, no
font/size/width combination has been confirmed to restore `base32g` on a
current Tesseract build. `base16g` (the default codec) is unaffected. See
`benchmarks/results/FONT_CANDIDATES.md` "Local font/size sweep (2026-07-23)"
for the full data. Use `base16g` until this is re-resolved.

### Added

- **`glyphive info`** — lists what this install can actually do: registered
  vs. available codecs, compression methods, render formats, and OCR
  engines, plus an optional `--font <name>` check for whether a font would
  resolve for PDF output. Every registry already exposed this
  programmatically; nothing surfaced it via the CLI before. `--json` for
  scripting.
- **`--font` (PDF) resolves an installed system font by its TRUE family
  name, not just its filename.** Previously `--font Consolas` failed on
  Windows because the installed file is `consola.ttf` (filename stem
  `consola` != `"consolas"`), even though the font is present. Now falls
  back to reading each candidate's `name` table via `fontTools` — already a
  hard `fpdf2` dependency, so this costs nothing new — when the fast
  filename-stem match finds nothing. `tools/ocr_font_report.py` gets the
  same resolution for free.

### Fixed

- **The page-footer hash disagreed with the CRC on essentially every
  OCR-recovered line, not just noisy ones.** The footer hash was computed
  over the raw OCR'd line text, while the per-line CRC check validates a
  whitespace-normalized (interior-OCR-space-stripped) reconstruction of that
  same line — two different derived strings of one line, guaranteed to
  disagree whenever OCR drifted so much as one interior space, which it
  almost always does. Worse, the footer-hash reader never consulted the
  header's authoritative `nsym_line` field, so any codec using per-line
  Reed-Solomon parity (`-crc16-rs`, the common case) hit this on every page
  regardless of OCR noise. The footer hash is now computed over the same
  CRC-validated canonical reconstruction, so it agrees with the CRC whenever
  the line actually decoded correctly, and still flags a genuine page
  content change.
- **`extract --from-images` crashed on PDF/DOCX input** with a confusing
  `PIL.UnidentifiedImageError` deep in Pillow, instead of a clear error.
  Moot now that the flag is removed — the default auto-detect path was
  already the correct way to read those inputs.
- **PDF restore no longer rasterizes and OCRs a document that already has a
  usable text layer.** A PDF glyphive itself created (the `fpdf2`/`pdf`
  render extra) embeds real text glyphs; `extract`/`list` now read that
  layer directly via `pypdfium2` when a `#!glyphive` header is present in
  it, skipping rasterize+OCR entirely — cheaper and more reliable than OCR
  for glyphive's own PDF output. Falls back to rasterize+OCR for a PDF with
  no such text layer (e.g. a scanned/photographed document saved as PDF).

## [0.2.0] - 2026-07-22

Restore correctness release. Several format fixes remove failure modes that
could make a printed document unrecoverable, and a measurement campaign settled
the long-open question of whether trained OCR models are worth shipping (they
are not).

**Breaking (pre-1.0, no compatibility shim):** the wire format changed. The
identifier stays `v1` (magic `B1`, `_VERSION = 1`, codec names unchanged), but
documents produced by 0.1.0 do not decode with 0.2.0. Re-create any archive you
intend to keep.

### Highlights

- **No trained OCR model is needed for any codec.** `base16g` never required
  one; `base32g` no longer does either, because this release's format fixes
  moved it from model-required to stock-viable. The published
  `glyphive-ocrmodel-*` packages were trained on the wrong data and are not
  recommended for anything.
- **`base32g` is Courier-only on stock OCR** (measured 4–10pt across 3 fonts):
  it fails on OCR-B and DejaVu Sans Mono at every size. Documented so nobody
  picks it blind.
- **A very short final data line no longer decides whether a document
  restores.** This was a lottery every document ran at create time.

### Added

- **`glyphive train` (experimental)** — build an OCR model for a codec/font/size
  with the data-integrity gates that every previous attempt lacked: it verifies
  that each row image actually shows its paired text, aborts on any unencodable
  transcription or non-zero trainer skip ratio, derives a narrowed unicharset
  from the codec registry instead of inheriting the base model's ~112
  characters, and builds its own starter artifacts. Reported CER is labelled a
  proxy everywhere it appears; a produced model records `gate_verdict=UNGATED`
  because character error rate has repeatedly failed to predict restore. Not
  needed to use glyphive — it exists so future experiments are measurable.
- **Repo-resident E2E benchmark grid harness** (`benchmarks/e2e_grid.py`):
  create → rasterize → OCR → restore over a font-size × line-width × codec ×
  line-parity grid. Per-cell status is one of
  `restored`/`not-restored`/`not-built`/`error`; a configuration `create`
  refuses to build is excluded from every restore-rate denominator (reported
  as `n/m testable (k not built)`, or a loud `UNTESTED`), `--repeat N`
  aggregates across repeat documents, the corpus is a pinned checked-in
  fixture, and results JSON carries full provenance (commit, engine version,
  corpus digest). Harness logic is unit-tested with a fake OCR provider, no
  engine required.
- **Physical-scan regression fixture + gate**
  (`tests/fixtures/physical_scan/`, wired into `tests/test_ocr_transcripts.py`):
  a committed, deterministically degraded page render (Gaussian blur 0.6 +
  0.7° rotation, generation script included) restored byte-for-byte through
  `extract --from-images` with `tesseract-glyphive`; skips cleanly without
  the engine. Guards the real-scan path with zero real user content.
- **Per-glyph deletion stats in `tools/ocr_font_report.py`**
  (`align_and_tally`): aligned-diff tallies of per-glyph drop rate and
  insertion adjacency alongside the existing confusion stats — OCR *dropping*
  a thin glyph shrinks the line and desynchronizes the fixed-width frame
  parse, a strictly worse failure than a substitution, so candidate alphabets
  are now gated on both.

### Fixed

- **A very short ("runt") final data line no longer decides restore.**
  Whether a document restored at small font sizes depended on
  `encoded_length mod line_capacity`: a few-character final data line is
  destroyed by OCR page segmentation (and can corrupt the line above it), so
  one payload byte more or less flipped restore between OK and FAIL
  (measured: 13-char final line fails, 153-char passes —
  `benchmarks/results/fourpt-runt-line-20260721.json`). The codec now
  zero-pads the protected stream so the final data line's printed payload is
  never below half the line width; the header's recorded length stays
  unpadded, so decode truncates the pad with no decoder change.
- **Group-packed decode no longer crashes on an out-of-range group.** For
  codecs where `radix**group_chars > 256**group_bytes` (basemaxg, base85,
  z85) an OCR misread could decode a group past its byte range;
  `int.to_bytes` then raised `OverflowError`, which sailed past every
  `ValueError` erasure handler and aborted the whole restore. Such a group
  now raises `ValueError` and is absorbed as an ordinary erasure.
- **`--descan` auto-retry no longer re-OCRs the sharp pass.** The failure-path
  retry ladder re-ran radius 0.0 over every page even though the initial
  sharp sweep was already computed; the retry now OCRs only the additional
  radii (0.6, 0.8) and merges onto the existing sharp results as the ordered
  spine — one full OCR sweep saved per retried document, byte-identical
  outcome.
- **`tools/ocr_font_report.py` whitelist quoting**: an alphabet containing a
  shell-special character (e.g. an embedded `"`) crashed pytesseract's
  internal `shlex.split` before OCR ran; the whitelist config is now
  shell-quoted.

### CI

- **PyPI publish and GitHub release no longer depend on the docs deploy.**
  A GitHub Pages/docs failure blocked the v0.1.0 package publish twice;
  `docs-build`/`docs-deploy` are now a parallel, best-effort branch off the
  release-critical path.

### Added (earlier in this cycle)

- **OCR per-character confidence drives char-level erasure marking (plan
  3).** A CRC-failed line used to erase its ENTIRE byte span for the
  document-level Reed-Solomon tier, even though the typical cause is one or
  two misread characters — the erasure budget was consumed far faster than
  the true error mass. `OcrProvider.ocr_image` now returns
  `List[OcrLine]` (text + optional per-character confidence, 0..1 or
  `None`) instead of `List[str]` — a breaking change to the provider
  contract, fine pre-1.0. Tesseract builds confidence from
  `image_to_data` (word-granularity, broadcast per character — Tesseract's
  stable API has no finer level); EasyOCR/PaddleOCR broadcast their own
  per-segment/per-line score. `Base16GCodec.decode_spool` accepts an
  optional `char_conf` (keyed by physical line order, since a CRC-failed
  line's own index label may be corrupt): for a failed line with usable
  confidence, only the byte offsets its low-confidence characters map to
  are marked as erasures — the line's other bytes enter the RS stream as
  ordinary (unverified) data. A two-pass, block-local safety valve makes
  this strictly no-worse than today: if a block still fails RS with the
  narrower erasure set, it is retried with the touching line(s) promoted
  to a full-span erasure (today's behaviour) before giving up. This is a
  hint about erasure *position* only — acceptance is still CRC/RS/SHA-256,
  never guessed. `char_conf` absent (the default) is byte-identical to a
  build without this feature. New `tools/conf_calibration.py` measures
  `P(char wrong | conf < t)` and recall of wrong characters against
  Tesseract to calibrate the default threshold (ships at `0.6` pending a
  real calibration run — see the tool's own docstring).

### Fixed (earlier in this cycle)

- **Default `--line-parity` (2) broke restore on the primary OCR path
  (breaking, pre-release — no compat shim).** The constrained Tesseract
  character whitelist used for scanning strips ALL interior spaces from
  every printed line, producing the "compact frame" form
  `split_frame`/`split_frame_with_parity` already tolerate. But the reader
  determined the printed per-line Reed-Solomon field's width by counting
  whitespace tokens per line (`_detect_line_parity_chars`), and a
  one-token compact line cannot vote — every line silently fell back to
  width 0, folding the line-parity characters into the payload and
  corrupting the byte count for the whole stream. At 3pt OCR-B end-to-end,
  `--line-parity 0` round-tripped fine while `--line-parity 2` (the
  default) and `4` both failed with `cannot recover RS parameters:
  data/parity line counts are inconsistent` or a per-line CRC/RS-budget
  error — the default was worse than off. The protected machine header
  (`layout.py`) now carries `nsym_line` as an authoritative field
  (`_machine_header_bytes`/`_decode_machine_header` gain one byte); the
  restore path (`restore/decode.py`) reads it from the CRC/RS-protected
  header — decoded before any payload line is even classified — and
  passes it straight to `RadixCodec.decode_spool`, which prefers it over
  the token-counting heuristic. `_detect_line_parity_chars` remains the
  fallback for headerless/raw-codec callers (e.g. `glyphive inspect`'s
  read-only `describe_line_stream`). Existing documents from before this
  change do not decode (the machine header envelope grew one byte).

### Changed (earlier in this cycle)

- **Page parity lifts the 255-page cap: `--parity-pages` now supports up to
  65,535 total pages (breaking, pre-release — no compat shim).** Document-level
  whole-page recovery (`codec/pagers.py`) previously required data pages +
  parity pages <= 255 (one Reed-Solomon symbol per byte, GF(2^8)) — too small
  for large archives (a 30 MB tree at ~3 KB/page is ~10,000 pages). Page
  parity now automatically switches to a GF(2^16) field (one symbol per pair
  of bytes) whenever the total exceeds 255, raising the cap to 65,535; the
  protected machine header gains a `pgpar_field` byte (8 or 16) recording
  which field a document uses, and `glyphive inspect` reports it. GF(2^8)
  documents (<=255 total pages) are unaffected byte-for-byte. Existing
  documents from before this change do not decode (the machine header
  envelope grew one byte to carry the new field).

- **Wire format hardened: interleaved parity, kind-covered CRC, optional
  per-line Reed-Solomon (breaking, pre-release — no compat shim).** The
  ``base16g-crc16-rs`` codec (and every denser radix codec sharing its
  engine) fixes three defects found while measuring recovery under a
  substitution-error channel, at zero size cost for the first two:
  (1) **parity byte interleave** — the document-level Reed-Solomon parity
  stream is now written symbol-major (parity byte *j* of block *b* at
  ``j*nblocks+b`` instead of ``b*nsym+j``), so one corrupted parity LINE
  spreads its damage across every block instead of wiping one block's entire
  parity budget outright; (2) **kind-covered CRC** — the per-line check field
  (and the H/T/Q machine frames in ``layout.py``) now covers the leading
  ``L``/``P``/``H``/``T``/``Q`` kind letter, so a misread that flips one kind
  into another now fails its own CRC instead of silently producing a
  CRC-valid phantom line; (3) **optional per-line Reed-Solomon** — each
  printed line may now carry ``nsym_line`` (0, 2, or 4; default 2, exposed as
  ``create --line-parity``) extra parity bytes over its own index token and
  payload, self-healing many single/double-character OCR errors in place
  before they ever touch the document-level RS erasure budget. The group
  header grew one byte (``B1 | version | nsym | nsym_line | orig_len``,
  8 → 9 bytes) to record the new field; existing documents from before this
  change do not decode. Measured effect: the recovery cliff (channel_sim,
  30 KB docs, 12% document parity) moves from failing at 0.1% substitution
  error to succeeding at 0.5% at the ``nsym_line=2`` default.

### Fixed (earlier in this cycle)

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
