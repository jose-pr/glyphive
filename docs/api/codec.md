# Codec API

Use `glyphive.codec.get(name)` to resolve a fresh codec instance. The built-in
wire identifier is `base16g-crc16-rs`.

::: glyphive.codec

## Codec engine and specs

The shared codec engine (`RadixCodec`, framing, CRC-16, Reed-Solomon, the
machine-frame API) lives in `glyphive.codec.engine`; every concrete codec
(`base16g-crc16-rs` and the denser family) is a thin spec-bearing subclass in
`glyphive.codec.radix`.

::: glyphive.codec.engine

::: glyphive.codec.radix
