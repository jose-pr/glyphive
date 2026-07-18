# Wire format

The current alpha format has three independent layers. Keeping them separate
lets each layer validate the kind of damage it understands.

```text
file tree
  -> archive record stream (GLYPHIV1, version 2)
  -> whole-stream compression (none, gzip, or zstd)
  -> base16c-crc16-rs data/parity frames
  -> protected header/footer frames and physical pages
```

The printable layout version and codec identifier are `1` and
`base16c-crc16-rs` respectively, but they are separate versioning points.

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

## `base16c-crc16-rs` payload frames

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

The protected byte stream begins with an eight-byte `base16c-crc16-rs` header:

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
#!glyphive v1 base16c-crc16-rs,gzip files=25 bytes=211233 pages=61
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
`base16c-crc16-rs` would make existing pages undecodable.
