"""Encoded line stream ⇄ paginated document with protected layout metadata.

This geometry-agnostic module groups codec ``L``/``P`` frames into physical
pages. Page 1 starts with a human-readable ``#!glyphive`` summary and then
fixed-width ``H`` machine-header frames. Every page ends in a ``T`` machine
footer followed by a human ``PAGE n/total`` hint on the same line.

Restore trusts only the H/T frames. They use the measured-safe 16-character
bootstrap alphabet and CRC-16 checks; the compact H-frame envelope additionally
carries its exact length and a digest. Thus the selected payload codec,
compression method, page count, and document digest are recoverable without
trusting or repairing unrestricted ASCII. The human representations may be
corrupted or clipped without changing machine interpretation.

H/T payload lines are capped at the same 60 safe characters used by codec data
frames. Footer page identity and the first 8 bytes of the SHA-256 of that page's
``"\\n"``-joined data frames live inside the protected T payload. Pages may be
read out of order; a missing or integrity-invalid metadata frame fails loud.
"""

from __future__ import annotations

import binascii
import hashlib
import itertools
import io
import struct
import typing as _ty

__all__ = [
    "HEADER_PREFIX",
    "PAGE_HASH_CHARS",
    "LayoutError",
    "MissingPageError",
    "Page",
    "format_header",
    "parse_header",
    "format_page_footer",
    "verify_page_footer",
    "page_data_hash",
    "paginate",
    "iter_paginate",
    "read_pages",
    "read_pages_to_spool",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Literal prefix that identifies the single-line document header.
HEADER_PREFIX: _ty.Final[str] = "#!glyphive"

#: Any line beginning with this marker is a display-only COMMENT and is skipped
#: wholesale on the read path (the ``#!glyphive`` summary is one such line; a
#: document may also carry additional ``#!`` notes). Restore trusts only the
#: CRC-protected H frames, so comments never carry authoritative data.
COMMENT_PREFIX: _ty.Final[str] = "#!"

#: Literal marker that identifies a per-page footer line.
_PAGE_MARKER: _ty.Final[str] = "PAGE"

#: This layout/format version (the header's ``v=`` value).
LAYOUT_VERSION: _ty.Final[int] = 1

#: Number of hex characters of the page SHA-256 kept in the footer.
PAGE_HASH_CHARS: _ty.Final[int] = 16

# Machine metadata is bootstrapped independently of the document's selected
# payload codec.  H/T are members of the measured-safe 16-character alphabet,
# while L/P remain reserved for payload data/parity frames.
_MACHINE_HEADER_KIND: _ty.Final[str] = "H"
_MACHINE_FOOTER_KIND: _ty.Final[str] = "T"
_MACHINE_PAYLOAD_CHARS: _ty.Final[int] = 60
_MACHINE_META_MAGIC: _ty.Final[bytes] = b"GH1"
_MACHINE_FOOTER_MAGIC: _ty.Final[bytes] = b"GT1"
_MACHINE_META_DIGEST_BYTES: _ty.Final[int] = 8
_MACHINE_HEADER_COPIES: _ty.Final[int] = 2
#: Reed-Solomon parity bytes appended after the header's data chunks. Sized to
#: fully reconstruct one lost/corrupted data chunk (60 safe chars = 30 bytes),
#: since CRC+duplication alone cannot recover a chunk whose copies are
#: identically misread by a deterministic OCR engine (see Known Facts).
_MACHINE_HEADER_PARITY_BYTES: _ty.Final[int] = 30

#: Header keys whose values are parsed/returned as ``int``.
_INT_KEYS: _ty.Final[_ty.FrozenSet[str]] = frozenset(
    {"v", "files", "bytes", "pages", "pgpar"}
)

#: Keys that MUST be recoverable from the compact display-only header line.
#: ``sha256`` and ``meta`` are intentionally NOT here — they live only in the
#: CRC-protected H frames now, not in the human summary.
_REQUIRED_KEYS: _ty.Final[_ty.Tuple[str, ...]] = (
    "v",
    "codec",
    "comp",
    "files",
    "bytes",
    "pages",
)


class LayoutError(ValueError):
    """Raised on a malformed header/footer or an unrecoverable page structure."""


class MissingPageError(LayoutError):
    """Raised when the footers show a page number is absent from the transcript.

    ``missing`` lists the 1-based page numbers that were not found.
    """

    def __init__(self, missing: _ty.Sequence[int], total: int) -> None:
        self.missing = list(missing)
        self.total = total
        joined = ", ".join(str(n) for n in self.missing)
        super().__init__(
            f"missing page(s) {joined} of {total}: transcript is incomplete"
        )


# ---------------------------------------------------------------------------
# Document header
# ---------------------------------------------------------------------------


def format_header(meta: _ty.Mapping[str, _ty.Any]) -> str:
    """Render the compact single-line, display-only document header from ``meta``.

    The line is *display-only* — restore reads authoritative metadata from the
    CRC-protected H frames, never from this summary — so it is kept minimal to
    waste as few OCR characters as possible:

        ``#!glyphive v<N> <codec>[,<comp>] files=<f> bytes=<b> pages=<p>[ pgpar=<k>]``

    ``v`` is a bare positional token (``v1``); codec and compression collapse to
    one positional ``codec[,comp]`` token (``,comp`` omitted when compression is
    ``none``/absent). ``sha256`` and ``meta`` are deliberately NOT emitted here
    (they live in the protected header). ``pgpar`` is emitted only when non-zero.
    ``meta`` must supply ``codec``, ``files``, ``bytes``, ``pages`` (and ``v``,
    defaulted to :data:`LAYOUT_VERSION`). No value may contain whitespace. The
    inverse is :func:`parse_header`.
    """
    version = meta.get("v", LAYOUT_VERSION)
    for key in ("codec", "files", "bytes", "pages"):
        if key not in meta:
            raise LayoutError(f"header meta is missing required key {key!r}")

    comp = str(meta.get("comp", "none"))
    codec_token = str(meta["codec"])
    if comp and comp != "none":
        codec_token = f"{codec_token},{comp}"

    tokens: _ty.List[str] = [HEADER_PREFIX, f"v{version}", codec_token]
    tokens.append(f"files={meta['files']}")
    tokens.append(f"bytes={meta['bytes']}")
    tokens.append(f"pages={meta['pages']}")
    if int(meta.get("pgpar", 0)) != 0:
        tokens.append(f"pgpar={meta['pgpar']}")

    for token in tokens[1:]:
        if any(ch.isspace() for ch in token):
            raise LayoutError(f"header token may not contain whitespace: {token!r}")
    return " ".join(tokens)


def parse_header(line: str) -> _ty.Dict[str, _ty.Any]:
    """Parse the compact display-only header line (inverse of :func:`format_header`).

    Grammar: ``#!glyphive v<N> <codec>[,<comp>] files=<f> bytes=<b> pages=<p>``.
    The first two non-prefix tokens are positional: a bare ``v<N>`` version and a
    ``codec[,comp]`` token (``comp`` defaults to ``none`` when absent). Remaining
    tokens are ``k=v``; integer keys (``files``/``bytes``/``pages``/``pgpar``) are
    coerced to ``int``. Tolerates *extra* unknown ``k=v`` tokens (forward-compat),
    returned as strings. Raises :class:`LayoutError` if the ``#!glyphive`` prefix,
    the positional version/codec tokens, or a required ``k=v`` key is missing, or
    if an integer key is non-numeric. Restore never trusts this summary;
    :func:`read_pages` uses only the protected H frames.
    """
    stripped = line.strip()
    tokens = stripped.split()
    if not tokens or tokens[0] != HEADER_PREFIX:
        raise LayoutError(
            f"not a glyphive header: line must start with {HEADER_PREFIX!r}"
        )

    body = tokens[1:]
    if len(body) < 2 or "=" in body[0] or "=" in body[1]:
        raise LayoutError(
            "glyphive header must begin with positional v<N> and codec[,comp] tokens"
        )
    meta: _ty.Dict[str, _ty.Any] = {}

    version_token = body[0]
    if not version_token.startswith("v"):
        raise LayoutError(
            f"header version token must look like 'v1', got {version_token!r}"
        )
    try:
        meta["v"] = int(version_token[1:])
    except ValueError:
        raise LayoutError(
            f"header version token must look like 'v1', got {version_token!r}"
        ) from None

    codec_name, _, comp_name = body[1].partition(",")
    meta["codec"] = codec_name
    meta["comp"] = comp_name or "none"

    for token in body[2:]:
        if "=" not in token:
            # Bare token (no '='): ignore for forward-compat rather than crash.
            continue
        key, value = token.split("=", 1)
        if key in _INT_KEYS:
            try:
                meta[key] = int(value)
            except ValueError:
                raise LayoutError(
                    f"header key {key!r} must be an integer, got {value!r}"
                ) from None
        else:
            meta[key] = value

    missing = [key for key in _REQUIRED_KEYS if key not in meta]
    if missing:
        raise LayoutError(
            "header is missing required key(s): " + ", ".join(missing)
        )
    if meta["v"] != LAYOUT_VERSION:
        raise LayoutError(
            f"unsupported layout version {meta['v']} "
            f"(this build handles {LAYOUT_VERSION})"
        )
    return meta


# ---------------------------------------------------------------------------
# Integrity-protected machine metadata
# ---------------------------------------------------------------------------


class _ParsedMachineFrame(_ty.NamedTuple):
    idx: _ty.Optional[int]
    payload: str
    ok: bool


def _machine_check(idx_token: str, payload: str) -> str:
    """Return a safe-alphabet CRC-16 for a machine metadata frame."""
    from .codec.base16c import nibble_encode

    canonical = idx_token.upper().encode() + payload.upper().encode()
    crc = binascii.crc_hqx(canonical, 0xFFFF)
    return nibble_encode(crc.to_bytes(2, "big"))


def _format_machine_frame(kind: str, idx: int, payload: str) -> str:
    from .codec.base16c import ALPHABET, encode_index

    if kind not in (_MACHINE_HEADER_KIND, _MACHINE_FOOTER_KIND):
        raise ValueError(f"invalid machine metadata frame kind {kind!r}")
    if any(char not in ALPHABET for char in payload):
        raise ValueError("machine metadata payload contains an unsafe character")
    token = encode_index(idx)
    return f"{kind}{token} {payload} #{_machine_check(token, payload)}"


def _parse_machine_frame(
    line: str, kind: str
) -> _ty.Optional[_ParsedMachineFrame]:
    """Parse one H/T frame without trusting any surrounding human text.

    Footer lines append a human ``PAGE n/total`` hint *after* the protected T
    frame.  H/T labels are therefore always the first token, keeping the entire
    authoritative frame within the same proven line width as L/P payload frames;
    clipping or corruption in the trailing hint is irrelevant.  Interior
    whitespace in the safe-alphabet payload is removed, exactly as it is for
    payload frames; the CRC remains the acceptance oracle.
    """
    from .codec.base16c import INDEX_WIDTH, decode_index, split_frame

    split = split_frame(line, allow_trailing=kind == _MACHINE_FOOTER_KIND)
    if split is None:
        return None
    label, payload, check_field = split
    if label[:1].upper() != kind:
        return None
    if len(label) != INDEX_WIDTH + 1:
        return _ParsedMachineFrame(idx=None, payload="", ok=False)

    idx_token = label[1:]
    idx = decode_index(idx_token)
    if idx is None:
        return _ParsedMachineFrame(idx=None, payload="", ok=False)

    payload = payload.upper()
    check = check_field[1:].upper()
    expected = _machine_check(idx_token, payload)
    return _ParsedMachineFrame(idx=idx, payload=payload, ok=check == expected)


def _pack_machine_text(value: _ty.Any, key: str) -> bytes:
    encoded = str(value).encode("utf-8")
    if not encoded or len(encoded) > 254:
        raise LayoutError(
            f"header value for {key!r} must encode to 1..254 bytes"
        )
    return bytes([len(encoded)]) + encoded


def _machine_uint(meta: _ty.Mapping[str, _ty.Any], key: str, bits: int) -> int:
    if key not in meta:
        raise LayoutError(f"header meta is missing required key {key!r}")
    value = meta[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise LayoutError(f"header key {key!r} must be an integer, got {value!r}")
    if not 0 <= value < 2**bits:
        raise LayoutError(
            f"header key {key!r} is outside the unsigned {bits}-bit range"
        )
    return value


def _machine_header_bytes(meta: _ty.Mapping[str, _ty.Any]) -> bytes:
    """Serialize authoritative header fields to a compact binary envelope."""
    version = meta.get("v", LAYOUT_VERSION)
    if version != LAYOUT_VERSION:
        raise LayoutError(f"unsupported layout version {version}")
    if "codec" not in meta or "comp" not in meta:
        missing = "codec" if "codec" not in meta else "comp"
        raise LayoutError(f"header meta is missing required key {missing!r}")

    sha_text = str(meta.get("sha256", ""))
    try:
        sha_bytes = bytes.fromhex(sha_text)
    except ValueError:
        sha_bytes = b""
    if len(sha_bytes) != 32 or len(sha_text) != 64:
        raise LayoutError("header sha256 must be exactly 64 hexadecimal characters")

    body = bytearray([version])
    body.extend(_pack_machine_text(meta["codec"], "codec"))
    body.extend(_pack_machine_text(meta["comp"], "comp"))
    if "meta" in meta:
        body.extend(_pack_machine_text(meta["meta"], "meta"))
    else:
        body.append(0xFF)
    body.extend(struct.pack(
        ">QQIII",
        _machine_uint(meta, "files", 64),
        _machine_uint(meta, "bytes", 64),
        _machine_uint(meta, "pages", 32),
        _machine_uint(meta, "pgpar", 32) if "pgpar" in meta else 0,
        _machine_uint(meta, "page_block_bytes", 32) if "page_block_bytes" in meta else 0,
    ))
    body.extend(sha_bytes)
    if len(body) > 0xFFFF:
        raise LayoutError("machine header metadata is too large")

    digest = hashlib.sha256(body).digest()[:_MACHINE_META_DIGEST_BYTES]
    return _MACHINE_META_MAGIC + len(body).to_bytes(2, "big") + body + digest


def _format_machine_header(meta: _ty.Mapping[str, _ty.Any]) -> _ty.List[str]:
    from .codec.base16c import _rs_encode, nibble_encode

    envelope = _machine_header_bytes(meta)
    _data, parity, _nblocks = _rs_encode(envelope, _MACHINE_HEADER_PARITY_BYTES)
    payload = nibble_encode(envelope)
    parity_payload = nibble_encode(parity)
    chunks = [
        payload[start:start + _MACHINE_PAYLOAD_CHARS]
        for start in range(0, len(payload), _MACHINE_PAYLOAD_CHARS)
    ]
    parity_chunks = [
        parity_payload[start:start + _MACHINE_PAYLOAD_CHARS]
        for start in range(0, len(parity_payload), _MACHINE_PAYLOAD_CHARS)
    ]
    chunks.extend(parity_chunks)
    frames = [
        _format_machine_frame(_MACHINE_HEADER_KIND, idx, chunk)
        for idx, chunk in enumerate(chunks)
    ]
    # A single OCR-damaged H line must not make the entire document
    # unrecoverable.  Keep identical, independently CRC-checked copies adjacent
    # so restore can accept either one without guessing at damaged metadata.
    return [frame for frame in frames for _copy in range(_MACHINE_HEADER_COPIES)]


def _take_machine_text(data: bytes, cursor: int, key: str) -> _ty.Tuple[str, int]:
    if cursor >= len(data):
        raise LayoutError(f"machine header is truncated before {key!r}")
    size = data[cursor]
    cursor += 1
    if size == 0 or cursor + size > len(data):
        raise LayoutError(f"machine header has an invalid {key!r} field")
    try:
        value = data[cursor:cursor + size].decode("utf-8")
    except UnicodeDecodeError:
        raise LayoutError(f"machine header {key!r} is not valid UTF-8") from None
    return value, cursor + size


def _num_header_parity_chunks() -> int:
    parity_chars = _MACHINE_HEADER_PARITY_BYTES * 2
    return -(-parity_chars // _MACHINE_PAYLOAD_CHARS)  # ceil div


def _decode_machine_header(
    frames: _ty.Sequence[_ParsedMachineFrame],
) -> _ty.Dict[str, _ty.Any]:
    from .codec.base16c import _rs_decode, nibble_decode, nibble_encode

    if not frames:
        raise LayoutError("no integrity-protected machine header found")
    indexed: _ty.Dict[int, str] = {}
    observed_indices: _ty.Set[int] = set()
    for frame in frames:
        if frame.idx is None:
            continue
        observed_indices.add(frame.idx)
        if not frame.ok:
            continue
        previous = indexed.get(frame.idx)
        if previous is not None and previous != frame.payload:
            raise LayoutError(f"conflicting machine header frame H{frame.idx}")
        indexed[frame.idx] = frame.payload
    if not observed_indices:
        raise LayoutError("machine header frames failed their integrity checks")

    num_parity_chunks = _num_header_parity_chunks()
    max_index = max(observed_indices)
    expected_indices = list(range(max_index + 1))
    missing_frames = sorted(set(expected_indices) - observed_indices)
    if missing_frames and max_index < num_parity_chunks:
        # Cannot tell data chunks from parity chunks without at least one
        # surviving frame at or past the parity boundary.
        raise LayoutError(
            f"machine header is missing frame index(es) {missing_frames}"
        )
    data_indices = [i for i in expected_indices if i < max_index + 1 - num_parity_chunks]
    parity_indices = [i for i in expected_indices if i >= max_index + 1 - num_parity_chunks]

    # Erasures: any expected index whose CRC-checked payload isn't available.
    erased = sorted(set(expected_indices) - set(indexed))
    if erased:
        # Reconstruct via RS using the last-known-good chunk lengths; a chunk
        # shorter than the full width only ever occurs at the boundary
        # between data and parity, which is exactly index ``max_index`` when
        # a trailing partial chunk exists. Use zero-fill for erased chunks
        # (RS erasure correction ignores their content and repairs it).
        chunk_chars = _MACHINE_PAYLOAD_CHARS
        placeholder = "A" * chunk_chars  # 'A' == alphabet value 0; RS ignores erased content
        data_payload = "".join(
            indexed.get(i, placeholder) for i in data_indices
        )
        parity_payload = "".join(
            indexed.get(i, placeholder) for i in parity_indices
        )
        if len(data_payload) % 2 or len(parity_payload) % 2:
            raise LayoutError("machine header has an odd safe-alphabet payload length")
        data_bytes = bytearray(nibble_decode(data_payload, len(data_payload) // 2))
        parity_bytes = bytearray(nibble_decode(parity_payload, len(parity_payload) // 2))
        data_erasure_bytes: _ty.List[int] = []
        parity_erasure_bytes: _ty.List[int] = []
        bytes_per_chunk = chunk_chars // 2
        for i in erased:
            if i < len(data_indices):
                start = i * bytes_per_chunk
                data_erasure_bytes.extend(range(start, min(start + bytes_per_chunk, len(data_bytes))))
            else:
                local = i - len(data_indices)
                start = local * bytes_per_chunk
                parity_erasure_bytes.extend(range(start, min(start + bytes_per_chunk, len(parity_bytes))))
        try:
            raw_bytes = _rs_decode(
                data_bytes,
                data_erasure_bytes,
                parity_bytes,
                parity_erasure_bytes,
                _MACHINE_HEADER_PARITY_BYTES,
                1,
            )
        except Exception as exc:  # pragma: no cover - reedsolo error type varies
            raise LayoutError(
                "machine header frame copies failed their integrity checks at "
                f"index(es) {erased}"
            ) from exc
        raw = bytes(raw_bytes)
    else:
        encoded = "".join(indexed[idx] for idx in data_indices)
        if len(encoded) % 2:
            raise LayoutError("machine header has an odd safe-alphabet payload length")
        try:
            raw = nibble_decode(encoded, len(encoded) // 2)
        except ValueError as exc:
            raise LayoutError(f"machine header payload is invalid: {exc}") from None

    prefix_size = len(_MACHINE_META_MAGIC) + 2
    if len(raw) < prefix_size + _MACHINE_META_DIGEST_BYTES:
        raise LayoutError("machine header envelope is truncated")
    if raw[:len(_MACHINE_META_MAGIC)] != _MACHINE_META_MAGIC:
        raise LayoutError("machine header has invalid magic")
    body_size = int.from_bytes(raw[len(_MACHINE_META_MAGIC):prefix_size], "big")
    expected_size = prefix_size + body_size + _MACHINE_META_DIGEST_BYTES
    if len(raw) != expected_size:
        raise LayoutError(
            f"machine header envelope length mismatch: expected {expected_size}, "
            f"got {len(raw)}"
        )
    body = raw[prefix_size:prefix_size + body_size]
    digest = raw[-_MACHINE_META_DIGEST_BYTES:]
    expected_digest = hashlib.sha256(body).digest()[:_MACHINE_META_DIGEST_BYTES]
    if digest != expected_digest:
        raise LayoutError("machine header envelope failed its integrity check")

    if not body or body[0] != LAYOUT_VERSION:
        version = body[0] if body else "truncated"
        raise LayoutError(f"unsupported machine header layout version {version}")
    cursor = 1
    codec_name, cursor = _take_machine_text(body, cursor, "codec")
    comp_name, cursor = _take_machine_text(body, cursor, "comp")
    if cursor >= len(body):
        raise LayoutError("machine header is truncated before 'meta'")
    if body[cursor] == 0xFF:
        profile = None
        cursor += 1
    else:
        profile, cursor = _take_machine_text(body, cursor, "meta")

    fixed_size = struct.calcsize(">QQIII") + 32
    if len(body) - cursor != fixed_size:
        raise LayoutError("machine header fixed fields have an invalid length")
    files, byte_count, pages, parity_pages, page_block_bytes = struct.unpack_from(
        ">QQIII", body, cursor
    )
    cursor += struct.calcsize(">QQIII")
    sha256 = body[cursor:cursor + 32].hex()
    if pages == 0:
        raise LayoutError("machine header page count must be positive")

    meta: _ty.Dict[str, _ty.Any] = {
        "v": LAYOUT_VERSION,
        "codec": codec_name,
        "comp": comp_name,
        "files": files,
        "bytes": byte_count,
        "pages": pages,
        "pgpar": parity_pages,
        "page_block_bytes": page_block_bytes,
        "sha256": sha256,
    }
    if profile is not None:
        meta["meta"] = profile
    return meta


# ---------------------------------------------------------------------------
# Per-page footer
# ---------------------------------------------------------------------------


def page_data_hash(page_lines: _ty.Sequence[str]) -> str:
    """Full hex SHA-256 of a page's data-line text block.

    The block is ``"\\n".join(page_lines)`` — the codec-framed lines carried by
    the page, in printed order. The footer keeps only the first
    :data:`PAGE_HASH_CHARS` characters of this digest.
    """
    block = "\n".join(page_lines)
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def format_page_footer(
    n: int, total: int, page_lines: _ty.Sequence[str]
) -> str:
    """Render the per-page footer for page ``n`` of ``total``.

    ``page_lines`` are the codec-framed data/parity lines on this page (NOT the
    header, NOT the footer). Grammar: ``PAGE <n>/<total> sha256=<first16hex>``.
    """
    from .codec.base16c import nibble_encode

    if not (1 <= n <= total < 2**32):
        raise LayoutError(f"invalid page position {n}/{total}")
    digest = bytes.fromhex(page_data_hash(page_lines)[:PAGE_HASH_CHARS])
    payload = nibble_encode(
        _MACHINE_FOOTER_MAGIC + total.to_bytes(4, "big") + digest
    )
    machine = _format_machine_frame(_MACHINE_FOOTER_KIND, n - 1, payload)
    return f"{machine} {_PAGE_MARKER} {n}/{total}"


class _ParsedFooter(_ty.NamedTuple):
    n: int
    total: int
    digest: str  # truncated hex as printed


def _parse_footer(line: str) -> _ty.Optional[_ParsedFooter]:
    """Parse the protected T frame; human ``PAGE n/total`` is display-only.

    A structurally footer-shaped line whose CRC fails (a real OCR misread —
    unlike ``H`` frames, ``T`` carries no duplication or RS parity) returns
    ``None`` rather than raising: the human ``PAGE n/total`` hint is never
    substituted, but the page's footer is simply treated as unreadable/absent
    for this pass. ``read_pages``'s existing missing-page detection (which
    compares the protected header's total page count against every page
    number actually confirmed by a *valid* footer) still catches a genuinely
    lost page; it does not depend on guessing at a damaged footer.
    """
    from .codec.base16c import nibble_decode

    frame = _parse_machine_frame(line, _MACHINE_FOOTER_KIND)
    if frame is None:
        return None
    if not frame.ok or frame.idx is None:
        return None
    if len(frame.payload) % 2:
        raise LayoutError("machine page footer has an odd payload length")
    try:
        raw = nibble_decode(frame.payload, len(frame.payload) // 2)
    except ValueError as exc:
        raise LayoutError(f"machine page footer payload is invalid: {exc}") from None
    expected_size = len(_MACHINE_FOOTER_MAGIC) + 4 + PAGE_HASH_CHARS // 2
    if len(raw) != expected_size or not raw.startswith(_MACHINE_FOOTER_MAGIC):
        raise LayoutError("machine page footer has an invalid envelope")
    cursor = len(_MACHINE_FOOTER_MAGIC)
    total = int.from_bytes(raw[cursor:cursor + 4], "big")
    digest = raw[cursor + 4:].hex()
    n = frame.idx + 1
    if total == 0 or n > total:
        raise LayoutError(f"machine page footer has invalid position {n}/{total}")
    return _ParsedFooter(n=n, total=total, digest=digest)


def verify_page_footer(
    footer_line: str, page_lines: _ty.Sequence[str]
) -> bool:
    """Return True iff ``footer_line``'s hash matches ``page_lines``.

    A structurally invalid footer line returns ``False``. Comparison is
    case-insensitive on the hex digest.
    """
    try:
        parsed = _parse_footer(footer_line)
    except LayoutError:
        return False
    if parsed is None:
        return False
    expected = page_data_hash(page_lines)[:PAGE_HASH_CHARS]
    return parsed.digest.lower() == expected.lower()


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class Page(_ty.NamedTuple):
    """One physical page.

    Attributes
    ----------
    number:
        1-based page number.
    total:
        Total page count of the document.
    text_lines:
        Every text line that goes on this physical page, in order: the document
        header first (page 1 only), then the codec-framed lines, then the PAGE
        footer last. This is what a renderer prints.
    encoded_lines:
        Just the raw codec-framed (``L``/``P``) lines this page carries — i.e.
        ``text_lines`` without the header/footer. This is what feeds
        :func:`codec.decode`.
    """

    number: int
    total: int
    text_lines: _ty.List[str]
    encoded_lines: _ty.List[str]


def _page_count(
    n_encoded: int, lines_per_page: int, *, first_page_overhead: int
) -> int:
    """Total pages needed for ``n_encoded`` codec lines given the per-page budget.

    Page 1 spends 1 line on the header and 1 on the footer (capacity - 2);
    every other page spends 1 line on the footer (capacity - 1). We compute the
    count directly by consuming the budget page by page — cheap and exact,
    avoiding the closed-form edge cases around the first page's extra overhead.
    """
    if lines_per_page <= first_page_overhead:
        raise ValueError(
            "lines_per_page must fit the human header, protected machine header, "
            "footer, and at least one data line; "
            f"got {lines_per_page}, need at least {first_page_overhead + 1}"
        )
    if n_encoded == 0:
        return 1  # a header+footer-only page still exists (empty archive)
    remaining = n_encoded
    pages = 0
    while remaining > 0:
        overhead = first_page_overhead if pages == 0 else 1
        capacity = lines_per_page - overhead
        remaining -= capacity
        pages += 1
    return pages


def paginate(
    encoded_lines: _ty.Sequence[str],
    meta: _ty.MutableMapping[str, _ty.Any],
    *,
    lines_per_page: int,
    parity_pages: int = 0,
    emit_human_header: bool = True,
) -> _ty.List[Page]:
    """Group ``encoded_lines`` into :class:`Page` objects with header/footer.

    ``encoded_lines`` are the framed lines from :func:`codec.encode` (already
    RS-interleaved — layout does NOT recompute FEC, it only chunks). ``meta`` is
    the header dict (``codec``/``comp``/``files``/``bytes``/``sha256`` …); this
    function fills in ``meta["pages"]`` with the final page count BEFORE the
    header is formatted, so the printed ``pages=`` matches the physical count.

    Chunking: each page's line budget is ``lines_per_page`` minus its overhead —
    2 on page 1 (document header + footer) and 1 on every other page (footer).
    The document header is the first ``text_line`` of page 1; the footer is the
    last ``text_line`` of every page.

    ``parity_pages`` (K) requests K additional whole-page-recovery pages after
    the data pages — see :func:`iter_paginate`.

    Returns the list of pages in order. Raises ``ValueError`` if
    ``lines_per_page`` is too small to fit header+footer+data.
    """
    encoded = list(encoded_lines)
    return list(
        iter_paginate(
            iter(encoded),
            len(encoded),
            meta,
            lines_per_page=lines_per_page,
            parity_pages=parity_pages,
            emit_human_header=emit_human_header,
        )
    )


def iter_paginate(
    encoded_lines: _ty.Iterable[str],
    n_encoded: int,
    meta: _ty.MutableMapping[str, _ty.Any],
    *,
    lines_per_page: int,
    parity_pages: int = 0,
    emit_human_header: bool = True,
) -> _ty.Iterator[Page]:
    """Yield pages without retaining the full encoded line or page lists.

    ``parity_pages`` (K, default 0) requests K additional document-level
    whole-page-recovery pages, emitted after the D data pages. Each data
    page's printed lines (``"\\n".join(page.encoded_lines)``, the same
    convention as :func:`page_data_hash`) form one RS "block", zero-padded to
    a common size B (the max data-page block length); K parity blocks are
    computed over the D data blocks (:mod:`glyphive.codec.pagers`) and printed
    as K additional pages carrying ``Q``-framed lines. Parity page numbers
    continue past the data pages (``D+1 .. D+K``); footers' ``total`` becomes
    ``D+K``. The machine envelope's ``pages`` field always stores D (the data
    page count); K and B are recorded separately as ``pgpar``/
    ``page_block_bytes``. K=0 (the default) reproduces the pre-parity-pages
    byte-for-byte output exactly: no ``Q`` frames, no behavior change.
    """
    if n_encoded < 0:
        raise ValueError("n_encoded must be non-negative")
    if parity_pages < 0:
        raise ValueError("parity_pages must be non-negative")
    # The compact machine envelope uses a fixed-width u32 for ``pages``, so its
    # frame count does not depend on the numeric page count.  A provisional value
    # therefore gives us the exact first-page overhead before pagination.
    meta["pages"] = 1
    meta["pgpar"] = parity_pages
    meta["page_block_bytes"] = 0
    provisional_machine_header = _format_machine_header(meta)
    # Page-1 overhead: (optional human header line) + protected machine header
    # frames + footer. When ``emit_human_header`` is False the ``#!glyphive``
    # line is omitted, so page 1 gains one data-line slot.
    human_header_lines = 1 if emit_human_header else 0
    first_page_overhead = human_header_lines + len(provisional_machine_header) + 1
    data_total = _page_count(
        n_encoded,
        lines_per_page,
        first_page_overhead=first_page_overhead,
    )
    grand_total = data_total + parity_pages

    from .codec import pagers as _pagers
    from .codec.base16c import split_frame

    if parity_pages and data_total + parity_pages > _pagers.MAX_TOTAL_BLOCKS:
        raise LayoutError(
            f"parity_pages={parity_pages} with {data_total} data page(s) "
            f"exceeds the {_pagers.MAX_TOTAL_BLOCKS}-page Reed-Solomon limit "
            f"(data pages + parity pages must be <= {_pagers.MAX_TOTAL_BLOCKS})"
        )

    # ``page_block_bytes`` (B) is a protected header field, but it depends on
    # every data page's contents (the max block length across all of them),
    # which are not known until all data lines are chunked. Page 1's header
    # is the very first thing yielded, so when K>0 we chunk all data pages up
    # front (consistent with the project's existing multi-pass, bounded-memory
    # create path -- see AGENTS.md) and reuse those chunks for emission below,
    # rather than consuming the encoded-line source twice. When K=0, none of
    # this runs: pages are chunked lazily exactly as before, so the K=0 path
    # is byte-for-byte identical to the pre-parity-pages format.
    precomputed_chunks: _ty.Optional[_ty.List[_ty.List[str]]] = None
    block_bytes = 0
    if parity_pages:
        source = iter(encoded_lines)
        remaining_n = n_encoded
        precomputed_chunks = []
        for page_no in range(1, data_total + 1):
            overhead = first_page_overhead if page_no == 1 else 1
            capacity = lines_per_page - overhead
            take = min(capacity, remaining_n)
            chunk = list(itertools.islice(source, take))
            remaining_n -= len(chunk)
            precomputed_chunks.append(chunk)
        block_bytes = max(
            (len("\n".join(chunk).encode("utf-8")) for chunk in precomputed_chunks),
            default=0,
        )

    meta["pages"] = data_total
    meta["page_block_bytes"] = block_bytes
    header_line = format_header(meta)
    machine_header = _format_machine_header(meta)
    if len(machine_header) != len(provisional_machine_header):
        raise LayoutError("machine header frame count changed during pagination")

    encoded = iter(()) if precomputed_chunks is not None else iter(encoded_lines)
    cursor = 0
    data_blocks: _ty.List[bytes] = []
    data_payload_width = 0
    for page_no in range(1, data_total + 1):
        if precomputed_chunks is not None:
            chunk = precomputed_chunks[page_no - 1]
            cursor += len(chunk)
        else:
            overhead = first_page_overhead if page_no == 1 else 1
            capacity = lines_per_page - overhead
            chunk = list(itertools.islice(encoded, min(capacity, n_encoded - cursor)))
            cursor += len(chunk)

        text_lines: _ty.List[str] = []
        if page_no == 1:
            if emit_human_header:
                text_lines.append(header_line)
            text_lines.extend(machine_header)
        text_lines.extend(chunk)
        text_lines.append(format_page_footer(page_no, grand_total, chunk))

        if parity_pages:
            block = "\n".join(chunk).encode("utf-8")
            data_blocks.append(block)
            for line in chunk:
                split = split_frame(line)
                payload = split[1] if split is not None else line
                data_payload_width = max(data_payload_width, len(payload))

        yield Page(
            number=page_no,
            total=grand_total,
            text_lines=text_lines,
            encoded_lines=chunk,
        )

    # Sanity: every encoded line was placed. A mismatch means the budget math
    # and the chunking disagree — fail loud rather than silently drop data.
    if cursor != n_encoded:
        raise LayoutError(
            f"internal pagination error: placed {cursor} of {n_encoded} "
            "encoded lines"
        )

    if parity_pages:
        from .codec.base16c import _frame_bytes

        padded_blocks = [b.ljust(block_bytes, b"\x00") for b in data_blocks]
        parity_blocks = _pagers.encode_page_parity(padded_blocks, parity_pages)
        # A parity line's PAYLOAD width matches the widest data-line payload
        # (not the full framed-line length -- that units bug printed Q rows
        # wider than the 60-char OCR-safe cap). Falls back to the safe default
        # width for an empty archive with no data lines.
        q_line_width = data_payload_width or _MACHINE_PAYLOAD_CHARS
        for offset, parity_block in enumerate(parity_blocks):
            page_no = data_total + 1 + offset
            q_lines = _frame_bytes("Q", parity_block, q_line_width)
            text_lines = list(q_lines)
            text_lines.append(format_page_footer(page_no, grand_total, q_lines))
            yield Page(
                number=page_no,
                total=grand_total,
                text_lines=text_lines,
                encoded_lines=q_lines,
            )

    try:
        next(encoded)
    except StopIteration:
        return
    raise LayoutError("encoded line iterator yielded more than n_encoded lines")


# ---------------------------------------------------------------------------
# Inverse: transcript text lines -> (header meta, encoded line list)
# ---------------------------------------------------------------------------


def _looks_like_encoded(line: str) -> bool:
    """Cheap check: does ``line`` look like a codec ``L<idx>``/``P<idx>`` frame?

    We do NOT validate the CRC here (that is codec.decode's job) — we only decide
    whether to keep the line as data. This delegates the structural split to
    ``codec.base16c.split_frame``, which anchors the label as the first token and
    ``#check`` as the last, joining everything between as the payload -- so an
    OCR-inserted interior space (splitting the payload into extra whitespace
    tokens) never causes a real encoded line to be dropped here. A line is kept
    if it has that shape, the label is ``L`` or ``P`` followed by a readable
    index token, and the check field starts with ``#``. This mirrors codec's
    ``_parse_line`` exactly (same shared helper) so the two can never drift
    apart again, while still ignoring headers/footers/noise (e.g. the
    ``PAGE 1/1 sha256=...`` footer has no ``#check`` field and is rejected).
    """
    from .codec.base16c import decode_index, split_frame

    split = split_frame(line)
    if split is None:
        return False
    label, _payload, _check = split
    if label[:1] not in ("L", "P", "Q"):
        return False

    return decode_index(label[1:]) is not None


def _is_frame_shaped_but_unreadable(line: str) -> bool:
    """True if ``line`` has the ``L``/``P``+``#check`` shape but a bad index token.

    This is exactly the OCR class (real-recovery findings #1/#2) where a stray
    inserted/leading character corrupts the *label* so ``decode_index`` rejects
    it. Such a line is NOT noise -- it is a real, addressable data/parity line
    the reader should be told about, not silently dropped.
    """
    from .codec.base16c import decode_index, split_frame

    split = split_frame(line)
    if split is None:
        return False
    label, _payload, _check = split
    if label[:1] not in ("L", "P", "Q"):
        return False
    return decode_index(label[1:]) is None


def read_pages(
    all_text_lines: _ty.Iterable[str],
) -> _ty.Tuple[_ty.Dict[str, _ty.Any], _ty.List[str]]:
    """Parse a full transcript back into ``(header_meta, encoded_lines)``.

    ``all_text_lines`` is every text line of a scanned/typed document — pages may
    be concatenated in any order and may repeat blank lines or OCR noise. This:

    1. Finds and parses the ``#!glyphive`` header (raises if none is present).
    2. Reads every ``PAGE n/total`` footer, using them to detect a *missing*
       page (raises :class:`MissingPageError` naming the absent page numbers) and
       to verify each page's data-block hash.
    3. Collects the codec-framed ``L``/``P`` lines and returns them (in transcript
       order — codec.decode re-sorts by embedded index, so order does not matter).

    Page-footer hash *mismatches* are advisory and collected separately in
    ``meta["_footer_hash_notes"]`` (they fire on essentially every OCR restore,
    because OCR-inserted spaces change the page-text hash while the L/P lines
    still decode via CRC/RS). They do NOT raise. Genuine page-integrity issues
    (reconstructed/missing pages) go in ``meta["_page_warnings"]``. A missing
    header raises, and a whole missing page raises only when it is unrecoverable
    (beyond the page-parity budget and no surviving lines).

    The returned ``meta`` is the parsed header dict plus:

    - ``meta["_page_warnings"]``     : real page-integrity warnings (missing/
      reconstructed pages) — worth surfacing at WARNING.
    - ``meta["_footer_hash_notes"]`` : advisory per-page footer-hash mismatches —
      expected on OCR input, surfaced quietly.
    - ``meta["_pages_seen"]``        : sorted list of page numbers found.
    """
    spool = io.BytesIO()
    header_meta, _count = read_pages_to_spool(all_text_lines, spool)
    spool.seek(0)
    return header_meta, [line.decode("utf-8").rstrip("\n") for line in spool]


def read_pages_to_spool(
    all_text_lines: _ty.Iterable[str], sink: _ty.BinaryIO
) -> _ty.Tuple[_ty.Dict[str, _ty.Any], int]:
    """Parse a transcript once and spool normalized codec lines sequentially."""

    # --- Pass 1: recover the authoritative protected header. ----------------
    # The unrestricted ``#!glyphive ...`` line is retained for humans and old
    # tooling, but the restore path never trusts it.  In particular, there is no
    # OCR-repair guessing of a garbled codec name (e.g. a misread character in
    # ``base16c-crc16-rs``): codec selection comes from CRC-checked H frames
    # encoded entirely in the measured-safe bootstrap alphabet.
    header_frames: _ty.List[_ParsedMachineFrame] = []
    warnings: _ty.List[str] = []
    # Footer-hash mismatches are ADVISORY and fire on essentially every OCR
    # restore (OCR inserts interior spaces that change the page-text hash while
    # the L/P lines still decode byte-for-byte via CRC/RS). They are kept
    # separate from real page-integrity warnings so the CLI can log them quietly
    # instead of crying wolf on a clean restore.
    footer_hash_notes: _ty.List[str] = []
    pages_seen: _ty.Dict[int, int] = {}
    block_hash = hashlib.sha256()
    block_count = 0
    # Per-page encoded lines (data ``L``/``P`` AND parity ``Q``), keyed by page
    # number, in the order encountered on that page. Needed so a missing data
    # page's block can be reconstructed from parity and re-injected at the
    # correct spool position -- writing straight to ``sink`` as lines are read
    # (the pre-parity-pages behavior) cannot do that, since a page might need
    # to be rebuilt only after every page has been seen.
    page_lines: _ty.Dict[int, _ty.List[str]] = {}
    current_page_lines: _ty.List[str] = []
    # Frame-shaped lines whose index token is unreadable (findings #1/#2): buffer
    # them for the current page block and flush to that page's number on its
    # footer, so the reader gets ``{page, raw}`` detail instead of a silent drop.
    unreadable_lines: _ty.List[_ty.Dict[str, _ty.Any]] = []
    pending_unreadable: _ty.List[str] = []
    for line in all_text_lines:
        frame = _parse_machine_frame(line, _MACHINE_HEADER_KIND)
        if frame is not None:
            header_frames.append(frame)
        stripped = line.strip()
        if not stripped:
            continue
        footer = _parse_footer(line)
        if footer is not None:
            expected = block_hash.hexdigest()[:PAGE_HASH_CHARS]
            if footer.digest.lower() != expected.lower():
                footer_hash_notes.append(
                    f"page {footer.n}/{footer.total}: footer hash "
                    f"{footer.digest!r} != computed {expected!r} "
                    f"(over {block_count} line(s))"
                )
            for raw in pending_unreadable:
                unreadable_lines.append({"page": footer.n, "raw": raw})
            pending_unreadable = []
            pages_seen[footer.n] = footer.total
            page_lines[footer.n] = current_page_lines
            current_page_lines = []
            block_hash = hashlib.sha256()
            block_count = 0
            continue
        if stripped.startswith(COMMENT_PREFIX):
            continue  # any '#!' line is a display-only comment, not a data line
        if _parse_machine_frame(line, _MACHINE_HEADER_KIND) is not None:
            continue  # protected machine header is not a payload line
        if _looks_like_encoded(line):
            encoded = stripped.encode("utf-8")
            current_page_lines.append(stripped)
            if block_count:
                block_hash.update(b"\n")
            block_hash.update(encoded)
            block_count += 1
        elif _is_frame_shaped_but_unreadable(line):
            pending_unreadable.append(stripped)
        # else: OCR noise / blank-ish junk — ignored.

    # Trailing frame-shaped-but-unreadable lines with no footer after them still
    # deserve reporting; their page number is unknown.
    for raw in pending_unreadable:
        unreadable_lines.append({"page": None, "raw": raw})

    # Any trailing encoded lines with no footer after them belong to a page
    # whose footer was itself dropped by OCR; they are not attributable to a
    # known page number, so they cannot be placed by :func:`read_pages_to_spool`'s
    # per-page reconstruction. They are lost from ``page_lines`` here (as they
    # were before parity pages existed) -- the missing-page detection below
    # still catches and reports the gap.

    # --- Missing-page detection via integrity-protected machine metadata. ---
    header_meta = _decode_machine_header(header_frames)
    data_total = header_meta["pages"]  # D: data pages only (machine envelope)
    parity_budget = header_meta.get("pgpar", 0)
    block_bytes = header_meta.get("page_block_bytes", 0)
    grand_total = data_total + parity_budget
    inconsistent = sorted(
        n for n, observed_total in pages_seen.items()
        if observed_total != grand_total
    )
    if inconsistent:
        raise LayoutError(
            "machine footer total disagrees with protected header on page(s) "
            + ", ".join(str(n) for n in inconsistent)
        )
    missing = [n for n in range(1, grand_total + 1) if n not in pages_seen]
    missing_data = [n for n in missing if n <= data_total]
    missing_parity = [n for n in missing if n > data_total]

    reconstructed_pages: _ty.Set[int] = set()
    if missing_data and parity_budget and len(missing_data) <= parity_budget:
        # Enough parity budget in principle -- attempt page-level RS recovery.
        # Gather every data/parity page's block bytes (``"\n".join(lines)``,
        # the same convention :func:`page_data_hash`/pagination use), in block
        # order ``0..D+K-1`` (index i == page number i+1); missing pages are
        # ``None`` erasures. Present parity pages must decode as clean ``Q``
        # frames for their block to be trustworthy input to reconstruction.
        from .codec import pagers as _pagers
        from .codec.base16c import decode_index, nibble_decode, split_frame

        def _q_block(lines: _ty.List[str]) -> _ty.Optional[bytes]:
            chunks: _ty.List[str] = []
            for line in lines:
                split = split_frame(line)
                if split is None:
                    return None
                label, payload, _check = split
                if label[:1] != "Q" or decode_index(label[1:]) is None:
                    return None
                chunks.append(payload)
            joined = "".join(chunks)
            if len(joined) % 2:
                return None
            try:
                return nibble_decode(joined, len(joined) // 2)
            except ValueError:
                return None

        blocks: _ty.List[_ty.Optional[bytes]] = []
        recoverable = True
        for n in range(1, grand_total + 1):
            if n in missing:
                blocks.append(None)
                continue
            if n <= data_total:
                block = "\n".join(page_lines.get(n, [])).encode("utf-8")
                blocks.append(block.ljust(block_bytes, b"\x00")[:block_bytes] if block_bytes else block)
            else:
                q_block = _q_block(page_lines.get(n, []))
                if q_block is None or len(q_block) != block_bytes:
                    recoverable = False
                    break
                blocks.append(q_block)

        if recoverable:
            try:
                rebuilt = _pagers.reconstruct_pages(blocks, parity_budget)
            except _pagers.PageParityError:
                rebuilt = None
            if rebuilt is not None:
                for n in missing_data:
                    block = rebuilt[n - 1]
                    text = block.rstrip(b"\x00").decode("utf-8")
                    lines = text.split("\n") if text else []
                    page_lines[n] = lines
                    reconstructed_pages.add(n)
                warnings.append(
                    "reconstructed missing data page(s) "
                    + ", ".join(str(n) for n in missing_data)
                    + " from page-parity"
                )

    still_missing_data = [n for n in missing_data if n not in reconstructed_pages]
    if still_missing_data:
        # Do NOT hard-fail here: a wholly missing page is just a contiguous
        # erasure burst in the encoded-line stream, and the codec's
        # document-wide interleaved Reed-Solomon can recover it outright when
        # the parity budget suffices (user decision 2026-07-17). Record the
        # gap and let codec.decode try; if the budget is exceeded it raises its
        # own named CodecError. Only when NO codec lines survived at all is the
        # transcript genuinely unrecoverable at this layer.
        joined = ", ".join(str(n) for n in still_missing_data)
        warnings.append(
            f"missing page(s) {joined} of {data_total}: relying on codec "
            "Reed-Solomon to recover them from the surviving pages"
        )

    # --- Write the encoded-line spool in page order (1..D), skipping parity
    # pages (D+1..D+K) entirely -- they never reach codec.decode. Writing in
    # page order (rather than transcript order) guarantees a reconstructed
    # interior/last page's lines land at the correct position in the spool,
    # which downstream RS-parameter recovery depends on (a missing *last*
    # page otherwise truncates the stream shape -- see Phase 0 findings).
    encoded_count = 0
    for n in range(1, data_total + 1):
        for stripped in page_lines.get(n, []):
            sink.write(stripped.encode("utf-8") + b"\n")
            encoded_count += 1

    if encoded_count == 0 and still_missing_data:
        raise MissingPageError(still_missing_data, data_total)
    if missing_parity:
        warnings.append(
            "missing parity page(s) "
            + ", ".join(str(n) for n in missing_parity)
            + f" of {grand_total}: parity pages carry no user data and are not "
            "reconstructed"
        )

    header_meta["_page_warnings"] = warnings
    header_meta["_footer_hash_notes"] = footer_hash_notes
    header_meta["_pages_seen"] = sorted(pages_seen)
    header_meta["_unreadable_lines"] = unreadable_lines
    header_meta["_missing_pages"] = missing
    header_meta["_reconstructed_pages"] = sorted(reconstructed_pages)
    return header_meta, encoded_count
