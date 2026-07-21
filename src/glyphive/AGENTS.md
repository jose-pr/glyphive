# `glyphive` — public API header

Header-file-style reference for the `glyphive` package: every public export
grouped by module, with its signature, arguments, return/contract, and
gotchas, so this package can be consumed without reading its source. Kept
current with the public API. For the project overview, code layout, and
concepts, see the repo-root [`AGENTS.md`](../../AGENTS.md).

Every optional renderer/OCR-engine import is gated inside functions:
`import glyphive`, `glyphive.render`, and `glyphive.restore.ocr` all succeed
with zero extras installed and only raise (naming the install hint) when the
missing feature is actually used. All filesystem access goes through
`pathlib_next.Path`, never `os`/`pathlib` directly.

## `glyphive`

- **`__version__`** — installed package version (`importlib.metadata`,
  falls back to `"0.0.0+unknown"` when not installed).

## `glyphive.archive` — tree ⇄ flat binary-safe byte stream

- **`archive_tree(root, *, use_ignore=True, extra_ignore=None, metadata="none") -> bytes`**
  — serialize the directory tree at `root` (`str`/`os.PathLike`/`Path`) into
  one deterministic archive-stream `bytes` blob (records sorted by relpath).
  `metadata="basic"` captures permission bits + mtime; `"none"` (default)
  omits them. Compression is a separate stage — see `glyphive.compression`.
- **`write_archive(root, sink, *, use_ignore=True, extra_ignore=None, metadata="none", chunk_size=1_048_576) -> None`**
  — streaming form of `archive_tree`; writes to a binary `sink` without
  buffering file contents in memory. Raises `ValueError` if a file is
  truncated or grows while being archived.
- **`list_paths(root, *, use_ignore=True, extra_ignore=None) -> list[str]`** —
  the sorted POSIX relpaths `archive_tree` would archive (empty directories
  get a trailing `/`).
- **`iter_records(data: bytes) -> Iterator[Record]`** — parse an archive
  stream into `Record(type, path, mode, mtime, content)` in stream order.
  Raises `ValueError` on truncation, unknown version/profile, unknown record
  type, or trailing bytes.
- **`iter_record_events(source, *, chunk_size=1_048_576, max_content_bytes=None) -> Iterator[RecordHeader | RecordChunk]`**
  — streaming parse: a `RecordHeader` followed by zero or more `RecordChunk`
  events totaling exactly `content_length`. `max_content_bytes` rejects an
  oversized declared record before its payload is read.
- **`stream_metadata(data: bytes) -> ArchiveMetadata`** — `(version, metadata)`
  parsed from a stream's header only.
- **`MAGIC = b"GLYPHIV1"`**, **`FORMAT_VERSION = 2`**, **`V1_FORMAT_VERSION = 1`**,
  **`METADATA_PROFILES = ("none", "basic")`**, **`REC_FILE = 0`**,
  **`REC_EMPTY_DIR = 1`** — wire constants.
- Ignore filter: `use_ignore=True` (default) reads **root-level only**
  `.gitignore`/`.ignore` (nested ignore files are not honored — v1
  limitation) plus `extra_ignore` pattern lines; `.git/` is always pruned.
  `use_ignore=False` disables all filtering.

## `glyphive.codec` — printable codecs (bytes ⇄ OCR-safe lines)

- **`get(name: str) -> Codec`** — a fresh registered codec instance, or
  `ValueError` naming the registered names.
- **`names() -> list[str]`** / **`available() -> list[str]`** — all
  registered codec names / those currently usable.
- **`Codec`** (ABC) — `encode(data: bytes, **options) -> list[str]`,
  `decode(lines: Iterable[str], **options) -> bytes`. Concrete subclasses
  self-register via `__init_subclass__`; a duplicate or invalid
  (non-lowercase-ASCII) `name` raises at class-definition time.
- Built-in codec classes (each a thin `Codec` subclass differing by alphabet
  spec): **`Base16GCodec`** (`base16g-crc16-rs`, default — measured-safe
  16-glyph alphabet `ABCDHKLMPRTVXY34`, no confusable aliases),
  `Base8GCodec`, `Base32GCodec`, `Base64GCodec`, `BaseMaxGCodec` (glyphive
  OCR-tuned alphabets), and `Base16Codec`/`Base32Codec`/`Base32CCodec`/
  `Base64Codec`/`Base85Codec`/`Z85Codec` (standard textbook alphabets).
  Denser-than-16 codecs need a matching trained OCR model for reliable
  restore of *scanned* input; creation never requires OCR.
- Every `L`/`P` printed line carries a masked index and a full CRC-16 in the
  same safe alphabet; a document-wide interleaved Reed-Solomon parity layer
  repairs scattered erasures. Decode never mutates data to make more of it
  decode — correctness rests only on per-line CRC/RS plus the caller's
  whole-document SHA-256 gate (`glyphive.restore.decode`).
- `encode` zero-pads the protected byte stream so the FINAL data line's
  printed payload is never below half the configured line width (a very short
  final line is destroyed by OCR page segmentation at small font sizes,
  measured in `benchmarks/results/fourpt-runt-line-20260721.json`). The
  header's recorded length stays unpadded, so decode truncates the pad away
  with no decoder involvement; padding may grow the emitted line count by at
  most one, and `encoded_line_count` reflects the padded total exactly.
- Decode corruption contract: any malformed printed payload — bad alphabet
  char, short group, or a group-packed digit sequence whose value exceeds the
  group's byte range (possible for `basemaxg`/`base85`/`z85` misreads) —
  raises `ValueError` and is absorbed as an erasure; it never escapes as
  `OverflowError` or any non-`ValueError`.

## `glyphive.compression` — named whole-stream compression

- **`get(name: str) -> CompressionMethod`**, **`names() -> list[str]`**,
  **`available() -> list[str]`** — same registry pattern as `codec`.
- **`default() -> str`** — `"zstd"` if the optional `zstandard` package is
  importable, else `"gzip"`.
- **`CompressionMethod`** (ABC) — `compress(data: bytes, level=None) -> bytes`,
  `decompress(data: bytes) -> bytes`; streaming variants
  `compress_stream(source, sink, *, level=None, chunk_size=1_048_576)` /
  `decompress_stream(source, sink, *, chunk_size=1_048_576)` (built-ins do
  bounded-memory I/O; external plugins may fall back to the one-shot form).
- Built-ins: **`NoneCompression`** (`"none"`), **`GzipCompression`**
  (`"gzip"`, stdlib), **`ZstdCompression`** (`"zstd"`, optional
  `zstandard`). Compression is applied once to the *whole* archive stream,
  never per file; the method used is recorded in the page header.

## `glyphive.layout` — encoded lines ⇄ paginated document

Geometry-agnostic pagination with CRC/RS-protected machine metadata; restore
trusts only `H` (header) and `T` (footer) frames, never the display-only
`#!glyphive` / `PAGE n/total` human text. Machine frames (`H`/`T`/`Q`) carry
their own fixed-width per-line Reed-Solomon field (independent of the
document's `--line-parity`, which the header itself is what communicates);
an in-line correction is trusted only after the printed CRC re-verifies.

- **`paginate(encoded_lines, meta, *, lines_per_page, parity_pages=0, emit_human_header=True) -> list[Page]`**
  / **`iter_paginate(encoded_lines, n_encoded, meta, *, lines_per_page, parity_pages=0, emit_human_header=True) -> Iterator[Page]`**
  — group codec `L`/`P` frames into `Page` objects, adding the header on
  page 1 and a footer on every page. `parity_pages=K` (default 0, off)
  appends K extra document-level parity pages (`Q` frame kind) after the
  data pages; `emit_human_header=False` omits the `#!glyphive` line
  entirely (restore is unaffected either way).
- **`read_pages(all_text_lines: Iterable[str]) -> tuple[dict, list[str]]`** —
  parse a full transcript (pages may be concatenated in any order, with
  blank lines/OCR noise) back into `(header_meta, encoded_lines)`. Raises
  `MissingPageError` (a `LayoutError` subclass) naming absent page numbers.
- **`read_pages_to_spool(all_text_lines, sink, *, line_conf=None) -> tuple[dict, int]`**
  — streaming form of `read_pages`; spools normalized codec lines to a
  binary sink instead of returning them in memory. `line_conf` (optional)
  is per-line OCR confidence, aligned by `read_pages`/`read_pages_to_spool`.
- **`format_header(meta: Mapping) -> str`** / **`parse_header(line: str) -> dict`**
  — the display-only `#!glyphive v<N> <codec>[,<comp>] files=<f> bytes=<b>
  pages=<p>[ pgpar=<k>]` line and its inverse. `parse_header` has no
  production caller since the SHA-256 moved into the `H` frames (kept for
  the public API and tests).
- **`page_data_hash(page_lines: Sequence[str]) -> str`** — full hex SHA-256
  of `"\n".join(page_lines)`.
- **`format_page_footer(n, total, page_lines) -> str`** /
  **`verify_page_footer(footer_line, page_lines) -> bool`** — render/check
  `PAGE <n>/<total> sha256=<first16hex>`. A footer-hash mismatch is
  advisory only (logged, not a page-integrity warning) — `L`/`P` lines
  carry their own independent CRC/RS.
- **`Page`** (NamedTuple) — one physical page: `number` (1-based), `total`,
  plus its rendered lines.
- **`LayoutError`** (base) / **`MissingPageError`** (subclass, names the
  missing page numbers) — raised by the read path.
- **`HEADER_PREFIX = "#!glyphive"`**, **`PAGE_HASH_CHARS = 16`** — constants.

## `glyphive.render` — pages → text / PDF / DOCX / QR / hybrid

- **`get(name: str) -> RenderFormat`**, **`names() -> list[str]`**,
  **`available() -> list[str]`** — registry accessors.
- **`render(pages: list[Page], out, fmt: str, *, font=None, font_size=11.0, page_margin_pt=DEFAULT_PAGE_MARGIN_PT, horizontal_alignment="left", character_spacing_pt=0.0) -> None`**
  — resolve `fmt` (`"text"` / `"pdf"` / `"docx"` / `"qr"` / `"hybrid"`) and
  render `pages` to `out` (path-like). `horizontal_alignment` one of
  `HORIZONTAL_ALIGNMENTS = {"left", "center", "justify"}`.
- **`lines_per_page_for(font_size, *, page_height_pt=792.0, page_margin_pt=DEFAULT_PAGE_MARGIN_PT) -> int`**
  — derive rows-per-page from font size and page geometry (min 3). Raises
  `ValueError` for non-positive `font_size` or geometry that leaves no room.
- **`RenderFormat`** (ABC) — `render(pages, out, *, font, font_size,
  page_margin_pt, horizontal_alignment, character_spacing_pt) -> None`.
  Exposes `payload_capacity` (safety-capped, ≤60 chars — the OCR-measured
  safe line width) and `geometric_payload_capacity` (uncapped physical
  fit); non-text formats return `None` from each.
- Constants: **`FORMATS`** (frozenset of registered names),
  **`DEFAULT_MONO_FONT = "Consolas"`**, **`DEFAULT_DOCX_FONT`** (same),
  **`DEFAULT_PDF_FONT = "dejavu-sans-mono"`**, **`DEFAULT_PAGE_MARGIN_PT = 36.0`**,
  **`MINIMAL_PAGE_MARGIN_PT = 12.0`** (may exceed some printers' hardware
  printable area).
- PDF font resolution order (`registered_pdf_font`): FPDF core family
  (`courier`/`helvetica`/`times`/`symbol`/`zapfdingbats`/`arial`) → a
  bundled font (`ocr-b`, `dejavu-sans-mono`) → an explicit `.ttf`/`.otf`
  path → an OS font-store lookup by filename stem. The renderer's default
  PDF font is **Courier** (measured best bytes/page at 8pt+, zero embed
  cost); switching it needs a ≥10% measured win on two OCR engines.

## `glyphive.restore` — decode + unarchive (re-exports)

- **`decode_document(text_lines, *, char_conf=None, conf_threshold=0.6, max_suspects=6) -> tuple[dict, bytes]`**
  (from `glyphive.restore.decode`) — a full page transcript →
  `(meta, raw_archive_bytes)`: `layout.read_pages` → codec decode →
  decompress → whole-document SHA-256 verification against the header.
  Raises `RestoreError` on a hash mismatch (never returns corrupt bytes),
  `layout.MissingPageError` on an absent page, or `glyphive.codec`'s
  `CodecError` (via the codec) on an unrecoverable line — all propagate
  unchanged and name the offending page/line. Corrupt-but-RS-recovered
  pages are non-fatal and surface in `meta["_page_warnings"]`.
- **`RestoreError`** — integrity failure the restore path refuses to paper
  over (SHA-256 mismatch, or in `unarchive_bytes`/`restore_document`: a
  path-traversal or unresolved-clobber violation). Message always names the
  concrete offender.
- **`unarchive_bytes(raw: bytes, dest, *, overwrite=False) -> list[str]`** —
  write a verified archive byte stream into `dest`; returns the relpaths
  written. Rejects absolute or traversal-escaping paths. `overwrite=False`
  (default) errors on an existing file with different bytes; an identical
  existing file is left as-is.
- **`restore_document(text_lines, dest, *, overwrite=False) -> list[str]`** —
  convenience: `decode_document` then `unarchive_bytes`. The single call the
  CLI's `extract` uses for a text transcript.
- **`transcript_from_images(path) -> bytes`** (from `glyphive.restore.qr`) —
  decode a GQ1 QR page image or a sorted direct-child image directory back
  into an exact transcript slice. Requires `glyphive[qr]`.
- **`QrTransportError`** — a QR symbol set is malformed, inconsistent, or
  incomplete (mixed/duplicate/corrupt/missing symbols).

### `glyphive.restore.decode` (module-level extras beyond the re-exports)

- **`decode_document_to_spool(text_lines, sink, *, max_output_bytes=None, chunk_size=1_048_576, temp_dir=None, char_conf=None, conf_threshold=0.6, max_suspects=6) -> dict`**
  — streaming form of `decode_document`; spools the verified raw archive to
  `sink` instead of returning `bytes`, bounding memory via disk staging.

### `glyphive.restore.unarchive` (module-level extras beyond the re-exports)

- **`unarchive_spool(raw_source, dest, *, overwrite=False, chunk_size=1_048_576, max_file_bytes=None, on_progress=None) -> list[str]`**
  — streaming `unarchive_bytes`: stages records privately, publishes only
  after full validation.
- **`restore_document_spooled(text_lines, dest, *, overwrite=False, temp_dir=None, chunk_size=1_048_576, max_output_bytes=None, on_progress=None, char_conf=None, ...) -> list[str]`**
  — streaming `restore_document`, used by the CLI for large restores.

## `glyphive.restore.ocr` — OCR provider registry + voting

Importing this subpackage pulls in no heavy optional dependencies; each
provider's real backend import is gated inside its methods.

- **`get(name: str) -> OcrProvider`**, **`names() -> list[str]`**,
  **`available() -> list[str]`** — registry accessors.
- **`available_engines() -> list[str]`** — available providers ordered by
  documented preference: `("paddle", "easyocr", "tesseract-glyphive",
  "tesseract")`, then any other registered/available names.
- **`ocr_image(image_path, *, engine=None) -> list[OcrLine]`** — OCR one
  image with a selected engine or the highest-preference available one.
  `engine=None` with nothing installed raises `RuntimeError` naming the
  install hint.
- **`ocr_pages(image_paths, *, engine=None) -> list[list[OcrLine]]`** — OCR
  several images, resolving the provider once.
- **`ocr_vote(image_path, *, engines: list[str]) -> list[OcrLine]`** —
  majority-vote across multiple engines' TEXT per line (confidence is
  carried through from whichever line's text won, never blended). CRC/RS
  remains the actual correctness oracle — voting is only a pre-filter hint.
- **`OcrLine`** (NamedTuple) — `text: str`, `char_conf: Optional[list[Optional[float]]]`
  (one entry per character of `text` when present, `None` otherwise).
- **`OcrProvider`** (ABC) — `ocr_image(image_path) -> list[OcrLine]`.
  Built-ins: `TesseractProvider` (`"tesseract"`), `TesseractGlyphiveProvider`
  (`"tesseract-glyphive"` — constrained whitelist profile, generally the
  strongest default for scanned/photographed input), `EasyOcrProvider`
  (`"easyocr"`), `PaddleProvider` (`"paddle"`, needs model downloads).

## `glyphive.plugins` — explicit third-party discovery

Discovery loads third-party code in-process; it is opt-in, cached
process-wide, and never runs during a normal `glyphive` import.

- **`discover() -> DiscoveryReport`** — load and validate every installed
  entry point in `glyphive.codecs` / `glyphive.compression` /
  `glyphive.render_formats` / `glyphive.ocr_providers`, once per process
  (cached; call again and get the same report). A bad candidate (wrong
  base class, name mismatch, load exception) is recorded in
  `report.errors` without preventing valid candidates from registering.
- **`DiscoveryReport`** — `loaded: tuple[PluginEntry, ...]`,
  `errors: tuple[PluginError, ...]`.
- **`PluginEntry`** — `group: str`, `name: str`, `distribution: str`.
- **`PluginError`** — `entry: PluginEntry`, `message: str`.
- A candidate's entry-point name must be a lowercase ASCII identifier and
  must exactly equal the loaded class's `name`. Installed plugin code runs
  with the same permissions as glyphive and is not sandboxed.

## `glyphive.cli` — the `glyphive` console script

- **`run(argv=None) -> int`** — console-script / `python -m glyphive` entry
  point. Expands a leading tar-style `-c`/`-x`/`-t` into `create`/
  `extract`/`list`, consumes a global `--plugins` flag (calls
  `glyphive.plugins.discover()` and prints non-fatal plugin errors to
  stderr), then dispatches via `duho.main`.
- **`Glyphive`** — the `duho` CLI root (`LoggingArgs` mixin: `-v`/`-q`/
  `--loglevel`); subcommands `Create`, `Extract`, `Inspect`, `List`.
- `create` selects via `--codec`, `--compression`, `--format`, `--metadata
  none|basic`; without `--format` the output filename picks text/PDF/DOCX.
  `extract`/`list` classify direct-child input by magic, then extension,
  then UTF-8 text, and accept `--ocr-engine`. Both accept `--temp-dir`,
  `--chunk-size`; extract/list also `--max-output-bytes`. QR input/output
  needs an explicit `--from-qr` / `--format qr|hybrid`.
