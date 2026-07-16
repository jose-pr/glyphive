# Wire format

The current alpha format has three independent layers. Keeping them separate
lets each layer validate the kind of damage it understands.

```text
file tree
  -> archive record stream (GLYPHIV1, version 2)
  -> whole-stream compression (none, gzip, or zstd)
  -> g1 data/parity frames
  -> protected header/footer frames and physical pages
```

The printable layout version and codec identifier are both currently `1`/`g1`,
but they are separate versioning points.

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

## `g1` payload frames

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

The protected byte stream begins with an eight-byte `g1` header:

```text
"G1" | version:u8 | parity_symbols:u8 | original_length:u32-big-endian
```

Reed-Solomon parity is computed over that header and the compressed archive
bytes. Blocks are interleaved across the document. A line whose CRC fails
becomes a known erasure, allowing the decoder to spend the stronger erasure
correction budget instead of guessing which character changed.

## Page layout

Each document also prints a human-readable summary:

```text
#!glyphive v=1 codec=g1 comp=gzip meta=none files=25 bytes=211233 pages=61 sha256=<64 hex>
```

This line is not authoritative. The same values are stored in one or more
checked `H` frames using the safe alphabet:

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

`H`/`T` frames have CRC and envelope/hash protection, but they are not currently
Reed-Solomon-corrected. A wholly missing page is detected rather than rebuilt.

## Compatibility rule

Treat the exact codec name, compression name, frame kinds, and alphabet as wire
data. A new alphabet or framing rule needs a new codec identifier and must be
validated with print/OCR/restore measurements; silently changing `g1` would
make existing pages undecodable.
