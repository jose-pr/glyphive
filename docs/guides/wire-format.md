# Wire format

The current alpha format has three independent layers. Keeping them separate
lets each layer validate the kind of damage it understands.

```text
file tree
  -> archive record stream (GLYPHIV1, version 2)
  -> whole-stream compression (none, gzip, or zstd)
  -> base16g-crc16-rs data/parity frames
  -> protected header/footer frames and physical pages
```

The printable layout version and codec identifier are `1` and
`base16g-crc16-rs` respectively, but they are separate versioning points.

## Archive record stream

New archives begin with:

| Field | Size | Meaning |
| --- | ---: | --- |
| Magic | 8 bytes | `GLYPHIV1` |
| Version | 1 byte | `2` for current archives |
| Metadata flags | 1 byte | `0` = `none`, `1` = `basic` |
| Record count | 4 bytes, little-endian | Number of records |

Each record contains a type, a length-prefixed UTF-8 POSIX relative path,
optional metadata, a 64-bit content length, and raw content. Type `0` is a file;
type `1` is an explicitly empty directory. Non-empty directories are implied by
their file paths.

All variable-length fields are length-prefixed. File bytes can therefore
contain the magic value or any delimiter without ambiguity. Version 1 archive
streams remain readable; they always carry their historical mode/time fields.

## Compression

Compression covers the entire archive stream, never each file separately. The
selected registry name (`none`, `gzip`, or `zstd`) is carried by the protected
machine header, so restore chooses the inverse deterministically.

## `base16g-crc16-rs` payload frames

The exact safe alphabet is:

```text
ABCDHKLMPRTVXY34
```

It has 16 symbols, so each character represents 4 bits. Decoding is
case-insensitive, but there are no visual-confusion aliases.

Data and parity lines use one grammar:

```text
<kind><index5> <payload> #<check4>
```

- `<kind>` is `L` for data or `P` for Reed-Solomon parity.
- `<index5>` is a zero-based stream index encoded as five safe characters. A
  fixed positional mask avoids low indices printing as repeated glyphs.
- `<payload>` is normally 60 safe characters; the last data frame may be
  shorter.
- `<check4>` stores the full CRC-16/CCITT (`0x1021`, initial `0xFFFF`) over the
  printed index token and payload. Four safe characters hold exactly 16 bits.

## Codec family

`base16g-crc16-rs` is the recommended default: 16 characters is the measured
stock-OCR-safe ceiling — no larger alphabet reads back reliably on an untrained
engine. glyphive registers a family of radix-parameterized codecs sharing the
same frame/RS pipeline, differing only in alphabet and bits per character, in
**two groups**:

**glyphive-tuned (OCR-safe / OCR-optimized)** — the `g` suffix marks a
glyphive-modified alphabet chosen from OCR measurement:

| codec | bits/char | stock OCR | trained model |
|-------|-----------|-----------|---------------|
| `base8g-crc16-rs`    | 3 | safe | safe |
| `base16g-crc16-rs`  | 4 | **safe (default)** | safe |
| `base32g-crc16-rs`  | 5 | ~14.8% CER | **0.0% CER** |

**standard (textbook alphabets)** — plain, well-known encodings for interop,
NOT OCR-tuned:

| codec | radix | alphabet |
|-------|-------|----------|
| `base16-crc16-rs`   | 16 | hex `0-9A-F` |
| `base32-crc16-rs`   | 32 | RFC 4648 `A-Z2-7` |
| `base32c-crc16-rs`  | 32 | Crockford (`0-9A-Z` minus `ILOU`) |
| `base64-crc16-rs`   | 64 | RFC 4648 `A-Za-z0-9+/` |
| `base85-crc16-rs`   | 85 | base85 (group-packed, ~7% denser than base64) |
| `z85-crc16-rs`      | 85 | ZeroMQ Z85 (group-packed) |

The glyphive-tuned family also includes `basemaxg-crc16-rs` (43-glyph
group-packed, the maximal OCR-distinct set — needs a trained model like base32g).

## Two byte↔char packing strategies

Codecs convert bytes to alphabet characters one of two ways:

- **bit-packing** (power-of-two radices: base8g/16/16g/32*/64): each char carries
  `log2(radix)` bits, MSB-first, final group zero-padded.
- **group-packing** (non-power-of-two radices: base85/z85/base-maxg): Ascii85-style
  — every *N* bytes map to *M* base-`radix` digits where `radix**M >= 256**N`
  (base85: 4 bytes → 5 chars; base-maxg: 6 → 9). Captures the fractional bit a
  power-of-two packer wastes.

The per-line index token and CRC check field are rendered in the same base-`radix`
digits either way. The **check-field delimiter** (`payload <delim>check`) is `#`
by default but is per-codec — an alphabet that contains `#` (base85, z85) uses a
free character (`,` and `\` respectively), since the delimiter must be a glyph
outside the payload alphabet.

`base32g` (**32 glyphive**, not RFC-4648 base32) is the base16g 16 plus distinct
letters/digits and OCR-safe punctuation `? @ ! & + =`:

```text
ABCDHKLMPRTVXY34EFGNUW2567?@!&+=
```

**Naming rule:** a `g`-suffixed codec (`base16g`, `base32g`, …) is a
glyphive-modified, OCR-measured alphabet; an un-suffixed one (`base16`, `base32`,
`base32c`, `base64`) is the standard textbook alphabet.

Codecs are **never gated** — `create --codec <name>` just maps bytes to
characters and never needs OCR, whether or not a matching model is installed.
But *reliable restore* of a wide alphabet (base32g/base64 and the standard 32/64
sets) needs a matching per-font fine-tuned OCR model (published as opt-in
`glyphive-ocrmodel-*` packages); stock OCR distinguishes only ~16 glyphs
reliably (measured ~43 per font for a curated maximal set, but only ~27 across
all fonts). `create` logs an advisory when a non-`base16g` codec is selected.
The index/check widths adjust per radix (e.g. base64 uses a 3-character check
field). Because the L/P payload alphabet differs from the base16g-encoded `H`
header, restore reads the selected codec name from the protected header first,
then parses payload frames with that codec's alphabet.

The protected byte stream begins with an eight-byte `base16g-crc16-rs` header:

```text
"B1" | version:u8 | parity_symbols:u8 | original_length:u32-big-endian
```

Reed-Solomon parity is computed over that header and the compressed archive
bytes. Blocks are interleaved across the document. A line whose CRC fails
becomes a known erasure, allowing the decoder to spend the stronger erasure
correction budget instead of guessing which character changed.

The default `parity_ratio=0.12` targets parity bytes across the complete
protected stream, not 12% independently multiplied by every block. Glyphive
chooses the per-block symbol count whose aggregate parity is closest to that
target while keeping every codeword within GF(255). Each block can correct up
to that many known erasures, or half as many errors whose positions are unknown.

Rows below use the default 60-character payload (30 bytes per row). Byte
percentages are parity bytes divided by protected bytes (input plus the
eight-byte codec header).

The correction columns are maxima when damage is distributed within every
block's budget; one concentrated block can fail sooner.

| Input bytes | Previous parity bytes / rows | Previous overhead | Current parity bytes / rows | Current overhead | Known erasures per block / distributed total | Unknown errors per block / distributed total |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 13 / 1 | 12.04% | 13 / 1 | 12.04% | 13 / 13 | 6 / 6 |
| 1,000 | 700 / 24 | 69.44% | 120 / 4 | 11.90% | 24 / 120 | 12 / 60 |
| 10,000 | 6,500 / 217 | 64.95% | 1,188 / 40 | 11.87% | 27 / 1,188 | 13 / 572 |
| 100,000 | 64,600 / 2,154 | 64.59% | 11,853 / 396 | 11.85% | 27 / 11,853 | 13 / 5,707 |
| 1,000,000 | 645,200 / 21,507 | 64.52% | 118,422 / 3,948 | 11.84% | 27 / 118,422 | 13 / 57,018 |

## Page layout

Each document also prints a compact, human-readable summary by default (omit it
with `create --no-header`):

```text
#!glyphive v1 base16g-crc16-rs,gzip files=25 bytes=211233 pages=61
```

The version is a bare `v<N>` token; codec and compression collapse to one
positional `codec[,comp]` token (the `,comp` part is dropped when compression is
`none`). This line is display-only and **not authoritative** — it deliberately
omits the SHA-256 and metadata profile, and any line beginning with `#!` is
treated as a comment on the read path. The authoritative values are stored in
one or more checked `H` frames using the safe alphabet:

```text
H<index5> <up to 60 safe chars> #<check4>
```

The machine-header envelope contains layout version, codec and compression
names, optional metadata profile, file and byte counts, total pages, and the
complete document SHA-256. It also carries its exact length and a truncated
SHA-256 of the envelope body.

Every page ends with a checked `T` frame and display-only page text:

```text
T<index5> <safe payload> #<check4> PAGE 3/61
```

The protected footer payload contains a zero-based page index, total page count,
and the first eight bytes of SHA-256 over that page's newline-joined `L`/`P`
frames. Pages may arrive out of order. Duplicate/conflicting identities, a
missing page, a bad page hash, or damaged machine metadata causes restore to
fail loudly.

`H`/`T` frames have CRC and envelope/hash protection. Each `H` frame is also
printed as two identical, independently CRC-checked copies
(`_MACHINE_HEADER_COPIES = 2`), and the header envelope carries one extra
Reed-Solomon parity chunk (`_MACHINE_HEADER_PARITY_BYTES = 30`) covering the
whole envelope. Duplication alone cannot recover a chunk whose two copies are
misread identically by a deterministic OCR engine — measured on the real
Tesseract 5.4.0 gate — so restore reconstructs one damaged data or parity
chunk via RS erasure correction before falling back to accepting a surviving
CRC-checked copy. Corruption spanning more than one distinct chunk still
fails loud rather than guessing. `T` frames remain CRC/duplication-only (no
RS): a wholly missing page is still detected rather than rebuilt, and a
footer hash mismatch is a non-fatal warning (the page's `L`/`P` frames carry
their own CRC/RS protection independently of the footer's advisory hash).

## Compatibility rule

Treat the exact codec name, compression name, frame kinds, and alphabet as wire
data. A new alphabet or framing rule needs a new codec identifier and must be
validated with print/OCR/restore measurements; silently changing
`base16g-crc16-rs` would make existing pages undecodable.
