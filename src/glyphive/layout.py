"""glyphive — encoded line stream ⇄ paginated document with integrity metadata.

This module is *geometry-agnostic*. It takes the framed line stream produced by
:mod:`glyphive.codec` (``L#####``/``P#####`` lines) and groups it into pages,
prepending a single-line document header (page 1) and appending a per-page
footer (every page). It knows nothing about fonts, DPI, or PDF — the physical
page capacity arrives as an integer ``lines_per_page`` that a renderer
computes from font/page geometry. Here a "line" is simply one text line.

It also provides the inverse (:func:`read_pages`) so restore/decode can turn a
scanned/typed transcript — possibly with pages concatenated out of order, or a
whole page missing — back into the raw encoded line list for :func:`codec.decode`.
Pages out of order are fine (codec re-sorts by embedded index); a *missing* page
is detected via the footers' ``n/total`` and raised.

Document header grammar (ONE line, first line of page 1)
========================================================
Shebang-style, single space-separated ``k=v`` tokens, all values in a set safe
for OCR / re-typing (no whitespace inside a value)::

    #!glyphive v=1 codec=g1 comp=<method> meta=none files=<N> bytes=<orig_len> pages=<M> sha256=<hex64>

- ``#!glyphive`` : literal prefix. Its presence identifies the header line.
- ``v``          : layout/format version (integer; ``1`` for this build).
- ``codec``      : codec id, ``g1`` (see :mod:`glyphive.codec`).
- ``comp``       : compression method, one of ``none`` / ``gzip`` / ``zstd``.
- ``meta``       : optional archive metadata profile, ``none`` or ``basic``;
                   absent in older headers.
- ``files``      : number of archived files (from ``archive.list_paths``).
- ``bytes``      : ORIGINAL (pre-compression) archive byte count.
- ``pages``      : total physical page count ``M`` of this document.
- ``sha256``     : hex-encoded (64 chars) SHA-256 of the ORIGINAL, pre-compression
                   archive bytes — whole-document integrity, checked on restore.

Tokens are ``key=value`` split on the FIRST ``=`` only (a value never contains a
space, but this keeps parsing robust). :func:`parse_header` tolerates *extra*
unknown ``k=v`` tokens for forward-compatibility and raises a clear error if the
``#!glyphive`` prefix or any required key is absent. Integer-typed keys
(``v``/``files``/``bytes``/``pages``) are returned as ``int``; the rest as ``str``.

Per-page footer grammar (ONE line, last line of every page)
===========================================================
::

    PAGE <n>/<total> sha256=<first16hex>

- ``PAGE``       : literal marker identifying a footer line.
- ``<n>``        : 1-based page number of this page.
- ``<total>``    : total page count (missing-page detection).
- ``sha256=``    : the FIRST 16 hex characters of the SHA-256 of this page's
                   *data-line text block* — the codec lines that live on this
                   page, joined by ``"\\n"`` in the order printed. Truncated to
                   16 chars so it is short to re-type while still detecting a
                   corrupt page before the (expensive) whole-document assembly.

The footer hash covers only the codec-framed lines carried by the page — NOT the
document header and NOT the footer itself — so it is reproducible from the page's
data block alone by :func:`verify_page_footer`.
"""

from __future__ import annotations

import hashlib
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
    "read_pages",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Literal prefix that identifies the single-line document header.
HEADER_PREFIX: _ty.Final[str] = "#!glyphive"

#: Literal marker that identifies a per-page footer line.
_PAGE_MARKER: _ty.Final[str] = "PAGE"

#: This layout/format version (the header's ``v=`` value).
LAYOUT_VERSION: _ty.Final[int] = 1

#: Number of hex characters of the page SHA-256 kept in the footer.
PAGE_HASH_CHARS: _ty.Final[int] = 16

#: Header keys whose values are parsed/returned as ``int``.
_INT_KEYS: _ty.Final[_ty.FrozenSet[str]] = frozenset(
    {"v", "files", "bytes", "pages"}
)

#: Header keys that MUST be present for a valid header.
_REQUIRED_KEYS: _ty.Final[_ty.Tuple[str, ...]] = (
    "v",
    "codec",
    "comp",
    "files",
    "bytes",
    "pages",
    "sha256",
)

#: Order the header tokens are emitted in (stable, human-scannable).
_HEADER_ORDER: _ty.Final[_ty.Tuple[str, ...]] = (
    "v",
    "codec",
    "comp",
    "meta",
    "files",
    "bytes",
    "pages",
    "sha256",
)

_OCR_CONFUSABLE_MAP: _ty.Final[_ty.Dict[str, str]] = {
    "o": "0",
    "O": "0",
    "i": "1",
    "I": "1",
    "l": "1",
    "L": "1",
}

_OCR_NORMALIZED_KEYS: _ty.Final[_ty.FrozenSet[str]] = frozenset(
    {"v", "files", "bytes", "pages", "sha256"}
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
    """Render the single-line document header from ``meta``.

    ``meta`` must supply every key in ``_REQUIRED_KEYS``: ``v`` (defaulted to
    :data:`LAYOUT_VERSION` if absent), ``codec``, ``comp``, ``files``, ``bytes``,
    ``pages``, ``sha256``. Values are stringified; no value may contain a space
    (that would break the ``k=v`` grammar). The inverse is :func:`parse_header`.
    """
    tokens: _ty.List[str] = [HEADER_PREFIX]
    for key in _HEADER_ORDER:
        if key == "v":
            value = meta.get("v", LAYOUT_VERSION)
        elif key == "meta" and key not in meta:
            # ``meta`` was added after the v1 header. Keep it optional so old
            # transcripts remain readable while new documents can identify
            # their archive policy explicitly.
            continue
        else:
            if key not in meta:
                raise LayoutError(f"header meta is missing required key {key!r}")
            value = meta[key]
        text = str(value)
        if any(ch.isspace() for ch in text):
            raise LayoutError(
                f"header value for {key!r} may not contain whitespace: {text!r}"
            )
        tokens.append(f"{key}={text}")
    return " ".join(tokens)


def _normalize_ocr_token(text: str) -> str:
    """Canonicalize common OCR confusions used by the printed header/footer."""
    return "".join(_OCR_CONFUSABLE_MAP.get(char, char) for char in text)


def parse_header(line: str) -> _ty.Dict[str, _ty.Any]:
    """Parse a document header line back into a dict (inverse of format_header).

    Tolerates *extra* unknown ``k=v`` tokens (forward-compat) — they are returned
    as-is (string values). Integer-typed keys are coerced to ``int``. Raises
    :class:`LayoutError` if the ``#!glyphive`` prefix or any required key is
    missing, or if an integer key is non-numeric.
    """
    stripped = line.strip()
    tokens = stripped.split()
    if not tokens or tokens[0] != HEADER_PREFIX:
        raise LayoutError(
            f"not a glyphive header: line must start with {HEADER_PREFIX!r}"
        )

    meta: _ty.Dict[str, _ty.Any] = {}
    for token in tokens[1:]:
        if "=" not in token:
            # Bare token (no '='): ignore for forward-compat rather than crash.
            continue
        key, value = token.split("=", 1)
        if key in _OCR_NORMALIZED_KEYS:
            value = _normalize_ocr_token(value)
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
    digest = page_data_hash(page_lines)[:PAGE_HASH_CHARS]
    return f"{_PAGE_MARKER} {n}/{total} sha256={digest}"


class _ParsedFooter(_ty.NamedTuple):
    n: int
    total: int
    digest: str  # truncated hex as printed


def _parse_footer(line: str) -> _ty.Optional[_ParsedFooter]:
    """Parse a footer line; return ``None`` if it is not a footer at all."""
    stripped = line.strip()
    parts = stripped.split()
    if len(parts) != 3 or parts[0] != _PAGE_MARKER:
        return None
    count, hash_token = parts[1], parts[2]
    if "/" not in count or not hash_token.startswith("sha256="):
        return None
    n_text, total_text = count.split("/", 1)
    n_text = _normalize_ocr_token(n_text)
    total_text = _normalize_ocr_token(total_text)
    if not (n_text.isdigit() and total_text.isdigit()):
        return None
    digest = _normalize_ocr_token(hash_token[len("sha256="):])
    return _ParsedFooter(n=int(n_text), total=int(total_text), digest=digest)


def verify_page_footer(
    footer_line: str, page_lines: _ty.Sequence[str]
) -> bool:
    """Return True iff ``footer_line``'s hash matches ``page_lines``.

    A structurally invalid footer line returns ``False``. Comparison is
    case-insensitive on the hex digest.
    """
    parsed = _parse_footer(footer_line)
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


def _page_count(n_encoded: int, lines_per_page: int) -> int:
    """Total pages needed for ``n_encoded`` codec lines given the per-page budget.

    Page 1 spends 1 line on the header and 1 on the footer (capacity - 2);
    every other page spends 1 line on the footer (capacity - 1). We compute the
    count directly by consuming the budget page by page — cheap and exact,
    avoiding the closed-form edge cases around the first page's extra overhead.
    """
    if lines_per_page < 3:
        raise ValueError(
            "lines_per_page must be >= 3 (header + footer + at least one data "
            f"line); got {lines_per_page}"
        )
    if n_encoded == 0:
        return 1  # a header+footer-only page still exists (empty archive)
    remaining = n_encoded
    pages = 0
    while remaining > 0:
        overhead = 2 if pages == 0 else 1  # page 1: header+footer; others: footer
        capacity = lines_per_page - overhead
        remaining -= capacity
        pages += 1
    return pages


def paginate(
    encoded_lines: _ty.Sequence[str],
    meta: _ty.MutableMapping[str, _ty.Any],
    *,
    lines_per_page: int,
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

    Returns the list of pages in order. Raises ``ValueError`` if
    ``lines_per_page`` is too small to fit header+footer+data.
    """
    encoded = list(encoded_lines)
    total = _page_count(len(encoded), lines_per_page)

    # Resolve the chicken/egg: page count is known now, so stamp it before we
    # render the header, then assert the header line we build actually fits.
    meta["pages"] = total
    header_line = format_header(meta)

    pages: _ty.List[Page] = []
    cursor = 0
    for page_no in range(1, total + 1):
        overhead = 2 if page_no == 1 else 1
        capacity = lines_per_page - overhead
        chunk = encoded[cursor:cursor + capacity]
        cursor += len(chunk)

        text_lines: _ty.List[str] = []
        if page_no == 1:
            text_lines.append(header_line)
        text_lines.extend(chunk)
        text_lines.append(format_page_footer(page_no, total, chunk))

        pages.append(
            Page(
                number=page_no,
                total=total,
                text_lines=text_lines,
                encoded_lines=list(chunk),
            )
        )

    # Sanity: every encoded line was placed. A mismatch means the budget math
    # and the chunking disagree — fail loud rather than silently drop data.
    if cursor != len(encoded):
        raise LayoutError(
            f"internal pagination error: placed {cursor} of {len(encoded)} "
            "encoded lines"
        )
    return pages


# ---------------------------------------------------------------------------
# Inverse: transcript text lines -> (header meta, encoded line list)
# ---------------------------------------------------------------------------


def _looks_like_encoded(line: str) -> bool:
    """Cheap check: does ``line`` look like a codec ``L<idx>``/``P<idx>`` frame?

    We do NOT validate the CRC here (that is codec.decode's job) — we only decide
    whether to keep the line as data. A line is kept if it splits into exactly 3
    whitespace tokens, the first is ``L`` or ``P`` followed by a readable index
    token, and the third starts with ``#``. This mirrors codec's ``_parse_line``
    shape test so we never drop a real encoded line, while still ignoring
    headers/footers/noise.
    """
    parts = line.split()
    if len(parts) != 3:
        return False
    label, _payload, check = parts
    if not check.startswith("#"):
        return False
    if label[:1] not in ("L", "P"):
        return False
    from .codec.g1 import decode_index

    return decode_index(label[1:]) is not None


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

    Page-footer hash *mismatches* are collected as warnings in
    ``meta["_page_warnings"]`` (a list of strings) and do NOT raise — the codec's
    RS may still repair a lightly corrupted page. A missing header or a missing
    whole page DO raise, because those are unrecoverable at this layer.

    The returned ``meta`` is the parsed header dict plus:

    - ``meta["_page_warnings"]`` : list of per-page hash-mismatch warning strings.
    - ``meta["_pages_seen"]``    : sorted list of page numbers found.
    """
    lines = list(all_text_lines)

    # --- Pass 1: locate the header. -----------------------------------------
    header_meta: _ty.Optional[_ty.Dict[str, _ty.Any]] = None
    for line in lines:
        if line.strip().startswith(HEADER_PREFIX):
            header_meta = parse_header(line)
            break
    if header_meta is None:
        raise LayoutError(
            f"no {HEADER_PREFIX!r} document header found in the transcript"
        )

    # --- Pass 2: walk the lines, splitting into per-page blocks by footer. ---
    # A page's data block is every encoded line seen since the previous footer
    # (or the header) up to and including that page's PAGE footer. This lets us
    # verify each footer's hash against exactly the block it covers, even when
    # pages are concatenated out of order.
    warnings: _ty.List[str] = []
    pages_seen: _ty.Dict[int, int] = {}  # page number -> observed total
    encoded_lines: _ty.List[str] = []
    current_block: _ty.List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        footer = _parse_footer(line)
        if footer is not None:
            expected = page_data_hash(current_block)[:PAGE_HASH_CHARS]
            if footer.digest.lower() != expected.lower():
                warnings.append(
                    f"page {footer.n}/{footer.total}: footer hash "
                    f"{footer.digest!r} != computed {expected!r} "
                    f"(over {len(current_block)} line(s))"
                )
            pages_seen[footer.n] = footer.total
            current_block = []
            continue
        if stripped.startswith(HEADER_PREFIX):
            continue  # the document header is not a data line
        if _looks_like_encoded(line):
            encoded_lines.append(stripped)
            current_block.append(stripped)
        # else: OCR noise / blank-ish junk — ignored.

    # Any trailing encoded lines with no footer after them are still real data
    # (a footer could have been dropped by OCR); they are already in
    # ``encoded_lines``. We only *warn* via the missing-page check below.

    # --- Missing-page detection via the footers' declared total. ------------
    if pages_seen:
        totals = set(pages_seen.values())
        declared_total = max(totals)  # tolerate an OCR'd-wrong total on one page
        # Prefer the header's page count if present and consistent.
        header_total = header_meta.get("pages")
        if isinstance(header_total, int) and header_total > 0:
            declared_total = max(declared_total, header_total)
        missing = [
            n for n in range(1, declared_total + 1) if n not in pages_seen
        ]
        if missing:
            raise MissingPageError(missing, declared_total)
    else:
        # No footers at all: fall back to the header's page count. If it claims
        # more than one page we cannot confirm none is missing — but with zero
        # footers we cannot name which, so only raise if the header says >1 page.
        header_total = header_meta.get("pages")
        if isinstance(header_total, int) and header_total > 1:
            raise MissingPageError(list(range(1, header_total + 1)), header_total)

    header_meta["_page_warnings"] = warnings
    header_meta["_pages_seen"] = sorted(pages_seen)
    return header_meta, encoded_lines
