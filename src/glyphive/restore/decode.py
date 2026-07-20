"""glyphive — text transcript → original archive bytes (the OCR-less path).

This module composes the three already-built layers to turn a printed/typed
page transcript back into the exact archive byte stream that :mod:`archive`
produced:

    text lines
        → :func:`glyphive.layout.read_pages`   (strip header/footers, re-order
                                                 pages, detect missing pages)
        → ``codec.get(header.codec).decode``   (per-line CRC + Reed-Solomon,
                                                 recovers the compressed payload)
        → ``compression.get(header.comp).decompress`` (per the header's ``comp=``)
        → **whole-document SHA-256 verification against the header**.

The SHA-256 gate is the point of this module: *no silent partial restore and no
silent corruption*. A decode that "mostly works" but whose bytes differ from the
original must be a loud, named failure, not a returned blob. So after
decompression we recompute
``sha256(raw)`` and compare it to the ``sha256=`` recorded in the page header;
a mismatch raises :class:`RestoreError` naming expected-vs-got, and the corrupt
bytes are never returned.

Every failure below this layer already names *what* and *where*:

- a missing page raises :class:`glyphive.layout.MissingPageError` (naming the
  absent page numbers) — we let it propagate unchanged;
- an unrecoverable line raises :class:`glyphive.codec.CodecError` (naming the
  exact ``L#####`` / ``P#####`` line) — we let it propagate unchanged;
- a whole-document hash mismatch raises :class:`RestoreError` here.

Corrupt-but-*recovered* pages (footer-hash warnings that the codec's RS still
repaired) are not fatal: :func:`glyphive.layout.read_pages` records them in
``meta["_page_warnings"]`` and we carry that straight through in the returned
``meta`` so the caller (the CLI) can surface them to a human.
"""

from __future__ import annotations

import hashlib
import io
import logging as _logging
import tempfile
import typing as _ty

_logger = _logging.getLogger("glyphive.restore")

from .. import archive, codec, compression, layout

__all__ = [
    "RestoreError",
    "decode_document",
    "decode_document_to_spool",
]


class RestoreError(Exception):
    """Raised on an integrity failure the restore path refuses to paper over.

    Used for whole-document SHA-256 mismatches (:func:`decode_document`) and
    path-traversal / clobber violations (:mod:`glyphive.restore.unarchive`).
    The message always names the concrete offender (expected-vs-got digest, or
    the offending relpath) so a failure is actionable, never silent.
    """


class _VerifiedWriter:
    def __init__(self, sink, limit: int):
        self.sink = sink
        self.limit = limit
        self.count = 0
        self.digest = hashlib.sha256()

    def write(self, data):
        if self.count + len(data) > self.limit:
            raise RestoreError(
                f"decompressed archive exceeds maximum output size {self.limit} bytes"
            )
        written = self.sink.write(data)
        if written != len(data):
            raise OSError("archive spool accepted a partial write")
        self.count += written
        self.digest.update(data)
        return written


def _resolve_codec(name: str) -> codec.Codec:
    """Resolve the exact codec name from protected machine metadata."""
    return codec.get(name)


def decode_document(
    text_lines: _ty.Iterable[str],
    *,
    char_conf: "_ty.Optional[_ty.Sequence[_ty.Optional[_ty.Sequence[float]]]]" = None,
    conf_threshold: float = 0.6,
    max_suspects: int = 6,
) -> _ty.Tuple[_ty.Dict[str, _ty.Any], bytes]:
    """Turn a full page transcript back into ``(meta, raw_archive_bytes)``.

    Pipeline (each stage is an already-tested module; this only composes them):

    1. :func:`glyphive.layout.read_pages` — parse the ``#!glyphive`` header,
       strip page headers/footers, tolerate out-of-order pages, and detect a
       missing page. Propagates :class:`glyphive.layout.MissingPageError`
       (naming the absent page numbers) unchanged — that is the correct, loud
       behaviour for an incomplete transcript.
    2. ``codec.get(meta["codec"]).decode`` — per-line CRC + Reed-Solomon recovery of
       the compressed payload. Propagates :class:`glyphive.codec.CodecError`
       (naming the exact failing line) unchanged when a line is unrecoverable.
    3. :func:`glyphive.compression.get` — inverse of the archive's compression
       stage, selected by the header's ``comp=`` value.
    4. **Whole-document integrity**: ``sha256(raw)`` must equal the header's
       ``sha256=``. On mismatch this raises :class:`RestoreError` naming the
       expected and observed digests and returns nothing — corrupt bytes are
       never handed back — no silent corruption.

    ``char_conf`` (plan 3, optional): per-line RAW OCR character confidence,
    one entry per element of ``text_lines`` in the same order (``None`` for
    a line with no confidence) -- see :func:`decode_document_to_spool` and
    the module docstring's "OCR-confidence erasure hint" section in
    :mod:`glyphive.codec.engine`. Absent (the default), decode is byte-
    identical to a build without this feature.

    Returns
    -------
    ``(meta, raw)`` where ``raw`` is the archive byte stream ready for
    :func:`glyphive.restore.unarchive.unarchive_bytes`, and ``meta`` is the
    parsed header dict. ``meta`` still carries ``meta["_page_warnings"]`` (a
    list of corrupt-but-recovered-page warning strings from ``read_pages``) so
    the caller can log them. The returned ``meta["meta"]`` is the resolved
    archive profile; old document headers without that key are accepted.

    Raises
    ------
    glyphive.layout.LayoutError / MissingPageError:
        No parseable header, or a whole page absent from the transcript.
    glyphive.codec.engine.CodecError:
        A framed line failed CRC and RS could not correct it (line named).
    RestoreError:
        The decompressed archive's SHA-256 does not match the header's.
    """
    sink = io.BytesIO()
    meta = decode_document_to_spool(
        text_lines,
        sink,
        char_conf=char_conf,
        conf_threshold=conf_threshold,
        max_suspects=max_suspects,
    )
    return meta, sink.getvalue()


def decode_document_to_spool(
    text_lines: _ty.Iterable[str],
    sink: _ty.BinaryIO,
    *,
    max_output_bytes: _ty.Optional[int] = None,
    chunk_size: int = 1024 * 1024,
    temp_dir: _ty.Optional[str] = None,
    char_conf: "_ty.Optional[_ty.Sequence[_ty.Optional[_ty.Sequence[float]]]]" = None,
    conf_threshold: float = 0.6,
    max_suspects: int = 6,
) -> _ty.Dict[str, _ty.Any]:
    """Decode and stream-decompress a document into a seekable quarantine spool.

    ``char_conf`` (plan 3, optional): raw per-character OCR confidence, one
    entry per element of ``text_lines`` in the same order (``None`` for a
    line with no confidence, e.g. plain-text/DOCX input -- see
    :class:`glyphive.restore.ocr.OcrLine`). Threaded through
    :func:`glyphive.layout.read_pages_to_spool` (which re-orders it to match
    the codec-line spool's own order, surviving page reordering/
    reconstruction) down to the codec's ``decode_spool``, which uses it only
    to choose ERASURE POSITIONS for a CRC-failed line -- never to accept
    anything; see :mod:`glyphive.codec.engine`'s "OCR-confidence erasure
    hint" section. Absent (the default) or when the selected codec has no
    ``decode_spool`` support for it, decode is unaffected -- byte-identical
    to a build without this feature.
    """
    # 1) transcript -> (header meta, framed codec lines). read_pages raises
    #    MissingPageError (naming pages) / LayoutError (no header) — let it.
    with tempfile.TemporaryFile(dir=temp_dir) as encoded_spool, tempfile.TemporaryFile(
        dir=temp_dir
    ) as compressed_spool:
        meta, _encoded_count = layout.read_pages_to_spool(
            text_lines, encoded_spool, line_conf=char_conf
        )
        spool_conf = meta.pop("_line_conf", None)

        # Surface unreadable-index diagnostics NOW, before decode can fail on an
        # RS-budget error -- otherwise finding #5's whole point (tell the reader
        # *which* line broke the restore) is lost when decode raises first.
        for entry in meta.get("_unreadable_lines", []) or []:
            where = (
                f"page {entry['page']}" if entry.get("page") is not None
                else "unknown page"
            )
            _logger.warning(
                "unreadable frame index (%s): %r -- Reed-Solomon must recover "
                "this line's data from parity",
                where,
                entry.get("raw"),
            )

        # 2) Resolve the header's data identifier before decoding anything. Header
        # identifiers are data, not import paths, and unknown names must fail before
        # decompression or filesystem writes can begin.
        selected_codec = _resolve_codec(str(meta["codec"]))
        meta["codec"] = selected_codec.name
        encoded_spool.seek(0)
        # Capability check, not a name check: every radix codec inherits the
        # streaming, confidence-aware decode_spool, so all of them (not only
        # base16g) get the memory-bounded path and char-level erasure marking.
        if hasattr(selected_codec, "decode_spool"):
            selected_codec.decode_spool(
                encoded_spool,
                compressed_spool,
                temp_dir=temp_dir,
                char_conf=spool_conf,
                conf_threshold=conf_threshold,
                max_suspects=max_suspects,
            )
        else:
            lines = (
                line.decode("utf-8").rstrip("\r\n") for line in encoded_spool
            )
            compressed_spool.write(selected_codec.decode(lines))

        # 3) compressed payload -> raw archive bytes, per the header's method.
        comp = str(meta["comp"])
        expected_bytes = int(meta["bytes"])
        output_limit = expected_bytes if max_output_bytes is None else max_output_bytes
        if output_limit < expected_bytes:
            raise RestoreError(
                f"maximum output size {output_limit} is below protected document size "
                f"{expected_bytes}"
            )
        measured = _VerifiedWriter(sink, output_limit)
        compressed_spool.seek(0)
        compression.get(comp).decompress_stream(
            compressed_spool, measured, chunk_size=chunk_size
        )

    # 4) Whole-document integrity gate. NEVER return unverified bytes.
    expected = meta.get("sha256")
    got = measured.digest.hexdigest()
    if not expected:
        raise RestoreError(
            "header carries no sha256= digest; cannot verify document "
            "integrity (refusing to return unverified bytes)"
        )
    if got.lower() != str(expected).lower():
        raise RestoreError(
            "document integrity check failed: sha256 mismatch after decode/"
            f"decompress (comp={comp!r}) — expected {expected}, got {got}. "
            "The transcript decoded but does not reproduce the original bytes; "
            "refusing to return corrupt data."
        )

    if expected_bytes != measured.count:
        raise RestoreError(
            "document header byte count mismatch: "
            f"header claims {expected_bytes}, archive contains {measured.count}"
        )

    sink.seek(0)
    record_count = sum(
        isinstance(event, archive.RecordHeader)
        for event in archive.iter_record_events(
            sink, chunk_size=chunk_size, max_content_bytes=output_limit
        )
    )
    expected_records = meta["files"]
    if expected_records != record_count:
        raise RestoreError(
            "document header entry count mismatch: "
            f"header claims {expected_records}, archive contains {record_count}"
        )

    # The archive stream is the authority for the profile. A document header
    # may identify it explicitly, but an old header can omit the key.
    sink.seek(0)
    stream_meta = archive.stream_metadata(sink.read(len(archive.MAGIC) + 6))
    header_profile = meta.get("meta")
    if header_profile is not None:
        header_profile = str(header_profile)
        if header_profile not in archive.METADATA_PROFILES:
            raise RestoreError(
                f"header carries unknown metadata profile {header_profile!r}; "
                f"choose {', '.join(archive.METADATA_PROFILES)}"
            )
        if header_profile != stream_meta.metadata:
            raise RestoreError(
                "document/archive metadata profile mismatch: header "
                f"claims {header_profile!r}, stream carries "
                f"{stream_meta.metadata!r}"
            )
    else:
        header_profile = stream_meta.metadata
    meta["meta"] = header_profile
    meta["archive_version"] = stream_meta.version

    sink.seek(0)
    return meta
