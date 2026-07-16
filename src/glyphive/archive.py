"""glyphive — file tree ⇄ flat, binary-safe byte stream, plus the ignore
filter and the (separate) compression stage.

This module turns a directory tree into one deterministic ``bytes`` blob that
:mod:`glyphive.codec` encodes for print and :mod:`glyphive.layout` lays out on
pages; it also provides the inverse *parser*, ``iter_records`` (consumed by
:mod:`glyphive.restore.unarchive`). Serialization is flat and relpath-keyed and
restore recreates directories. Every field is length-prefixed binary framing, so
the stream survives arbitrary bytes — including a file whose own content happens
to contain the magic or any framing bytes — rather than relying on a text
delimiter that a payload could collide with.

All filesystem access goes through ``pathlib_next.Path`` (never ``os`` /
``pathlib`` directly).

Wire format (the "archive stream")
==================================
Everything is little-endian. ``struct`` format codes are given in parentheses.

    ┌──────────────────────────────────────────────────────────────────────┐
    │ MAGIC        8 bytes  = b"GLYPHIV1"                                    │
    │ VERSION      1 byte   (B)  = 2                                         │
    │ META_FLAGS   1 byte   (B)  0 = none, 1 = basic                        │
    │ REC_COUNT    4 bytes  (I)  number of records that follow               │
    │ record * REC_COUNT                                                     │
    └──────────────────────────────────────────────────────────────────────┘

Each record is:

    ┌──────────────────────────────────────────────────────────────────────┐
    │ REC_TYPE     1 byte   (B)  0 = file, 1 = explicitly-empty directory    │
    │ PATH_LEN     2 bytes  (H)  length of the UTF-8 relpath in bytes        │
    │ PATH         PATH_LEN bytes   relpath, UTF-8, "/"-separated (POSIX)    │
    │ [BASIC] MODE 2 bytes  (H)  permission bits (mode & 0o7777)             │
    │ [BASIC] MTIME 8 bytes (q) signed Unix milliseconds                     │
    │ CONTENT_LEN  8 bytes  (Q)  content length in bytes (0 for a dir)       │
    │ CONTENT      CONTENT_LEN bytes   raw file bytes (absent for a dir)     │
    └──────────────────────────────────────────────────────────────────────┘

Version 1 remains readable. Its header is ``VERSION`` + ``REC_COUNT`` and
every record unconditionally contains the historical 4-byte ``st_mode`` and
8-byte float ``st_mtime`` fields. Version 2 is the default for new archives;
``none`` omits optional metadata fields and ``basic`` stores only the ordinary
permission bits plus mtime rounded to integer milliseconds.

Because every variable-length field is length-prefixed, the content bytes are
never scanned for a delimiter and may contain *any* byte sequence, including
the magic or any framing bytes. Records are emitted in ascending ``relpath``
order (Python string sort on the POSIX relpath) so the stream is deterministic
for a given tree.

Directories are normally reconstructable from the file relpaths, so only
**explicitly-empty** directories (a directory with no files anywhere beneath
it) get their own ``REC_TYPE == 1`` record — this is what lets an empty dir
round-trip. Non-empty directories are implied by their files' paths.

Ignore filter
=============
When ``use_ignore`` is true (the default, matching the CLI's normal mode), a
``pathspec.PathSpec`` (``gitignore``) is built from ``.gitignore`` and
``.ignore`` found **at the tree root only** and from any ``extra_ignore``
pattern lines. Paths matching the spec (evaluated relative to the root, with a
trailing ``/`` for directories so ``dir/`` patterns match) are skipped, and the
``.git/`` directory is always skipped regardless. ``use_ignore=False`` disables
all ignore filtering (the CLI's ``--no-ignore``).

**Limitation (v1):** only root-level ``.gitignore`` / ``.ignore`` are read.
Nested ``.gitignore`` files deeper in the tree are *not* honored — out of scope
for v1.

Compression stage
=================
Compression is a **separate** stage from serialization. The whole
``archive_tree`` output is compressed by the caller, never per file.
``compress`` / ``decompress`` support ``"none"`` (passthrough), ``"gzip"``
(stdlib), and ``"zstd"`` (via the optional ``zstandard`` dependency, imported
lazily and only when requested). Which method was used is recorded in the page
header by :mod:`glyphive.layout`, not here.

gzip/deflate has no incremental validation — a single early error invalidates
everything downstream — so integrity is provided *around* the compressed stream
by the codec's per-line CRC and layout's per-page hashes, not by dropping
compression or adding per-file checks.
"""

from __future__ import annotations

import struct
import typing as _ty

from pathlib_next import Path

__all__ = [
    "MAGIC",
    "FORMAT_VERSION",
    "V1_FORMAT_VERSION",
    "METADATA_PROFILES",
    "REC_FILE",
    "REC_EMPTY_DIR",
    "Record",
    "ArchiveMetadata",
    "stream_metadata",
    "archive_tree",
    "list_paths",
    "iter_records",
]

# ---------------------------------------------------------------------------
# Wire format constants
# ---------------------------------------------------------------------------

MAGIC = b"GLYPHIV1"
V1_FORMAT_VERSION = 1
FORMAT_VERSION = 2

METADATA_PROFILES = ("none", "basic")
_METADATA_FLAGS = {"none": 0, "basic": 1}

REC_FILE = 0
REC_EMPTY_DIR = 1

# Fixed headers after MAGIC. Version 1 is retained for decoding old streams;
# version 2 adds the explicit metadata profile flag.
_HEADER_V1 = struct.Struct("<BI")
_HEADER_V2 = struct.Struct("<BBI")
_HEADER = _HEADER_V1
# Per-record fixed prefix: type (B) + path length (H).
_REC_PREFIX = struct.Struct("<BH")
# Version 1 record fields: full st_mode + float seconds + content length.
_REC_META_V1 = struct.Struct("<IdQ")
# Version 2 basic profile: permission bits + signed Unix milliseconds + length.
_REC_META_BASIC = struct.Struct("<HqQ")
_CONTENT_LEN = struct.Struct("<Q")

PathLike = _ty.Union[str, "Path", _ty.Any]


class Record(_ty.NamedTuple):
    """One parsed entry from an archive stream.

    ``mode`` and ``mtime`` are zero for version 2 ``metadata='none'`` records.
    Version 1 records retain their historical metadata values. ``content`` is
    ``b""`` for an empty-directory record (``type == REC_EMPTY_DIR``).
    """

    type: int
    path: str
    mode: int
    mtime: float
    content: bytes


class ArchiveMetadata(_ty.NamedTuple):
    """Metadata parsed from an archive stream header."""

    version: int
    metadata: str


# ---------------------------------------------------------------------------
# Ignore filter
# ---------------------------------------------------------------------------

_IGNORE_FILES = (".gitignore", ".ignore")


def _coerce_root(root: PathLike) -> "Path":
    """Coerce ``str`` / ``os.PathLike`` into a ``pathlib_next.Path``."""
    if isinstance(root, Path):
        return root
    return Path(root)


def _validate_root(root: PathLike) -> "Path":
    """Return an existing directory root that cannot redirect outside itself."""
    root = _coerce_root(root)
    if root.is_symlink():
        raise ValueError(f"archive root must not be a symbolic link: {root}")
    if not root.exists():
        raise FileNotFoundError(f"archive root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"archive root must be a directory: {root}")
    return root


def _reject_link(path: "Path", relposix: str) -> None:
    """Reject links because v2 has no link record and must not dereference them."""
    is_junction = getattr(path, "is_junction", None)
    if path.is_symlink() or (is_junction is not None and is_junction()):
        raise ValueError(
            f"archive path {relposix!r} is a symbolic link or junction; "
            "link records are not supported"
        )


def _build_ignore_spec(
    root: "Path",
    *,
    use_ignore: bool,
    extra_ignore: _ty.Optional[_ty.Sequence[str]],
):
    """Build a ``pathspec.PathSpec`` from root-level ignore files + extras.

    Returns ``None`` when ``use_ignore`` is false and no ``extra_ignore`` is
    given (nothing to filter). ``.git/`` is handled separately by the walk and
    is not part of this spec.
    """
    if not use_ignore and not extra_ignore:
        return None

    import pathspec  # local import; only needed when filtering

    lines: list[str] = []
    if use_ignore:
        for name in _IGNORE_FILES:
            candidate = root / name
            try:
                if candidate.is_file():
                    text = candidate.read_text(encoding="utf-8", errors="replace")
                    lines.extend(text.splitlines())
            except OSError:
                # An unreadable ignore file is simply skipped.
                continue
    if extra_ignore:
        lines.extend(extra_ignore)

    if not lines:
        return None
    return pathspec.PathSpec.from_lines("gitignore", lines)


def _is_ignored(spec, relposix: str, *, is_dir: bool) -> bool:
    """Return True if ``relposix`` matches the ignore spec.

    Directories are tested both bare and with a trailing ``/`` so ``dir/``
    gitignore patterns match a directory entry.
    """
    if spec is None:
        return False
    if spec.match_file(relposix):
        return True
    if is_dir and spec.match_file(relposix + "/"):
        return True
    return False


# ---------------------------------------------------------------------------
# Walk / collect
# ---------------------------------------------------------------------------


def _relposix(root: "Path", path: "Path") -> str:
    """POSIX-style relpath of ``path`` under ``root`` (``as_posix`` on the rel)."""
    return path.relative_to(root).as_posix()


def _walk_entries(
    root: "Path",
    *,
    use_ignore: bool,
    extra_ignore: _ty.Optional[_ty.Sequence[str]],
) -> _ty.Iterator[_ty.Tuple[str, "Path", bool]]:
    """Walk ``root`` yielding ``(relposix, path, is_empty_dir)`` for kept entries.

    Files are yielded with ``is_empty_dir=False``; directories that end up with
    no kept descendant files are yielded once with ``is_empty_dir=True`` so an
    empty directory round-trips. ``.git`` and ignore-matched paths are pruned.
    Ordering here is walk order; the caller sorts for a deterministic stream.
    """
    spec = _build_ignore_spec(
        root, use_ignore=use_ignore, extra_ignore=extra_ignore
    )

    # Track, per directory relpath, whether any file was emitted at/under it,
    # so we can emit an empty-dir record only for the genuinely empty ones.
    for dirpath, dirnames, filenames in root.walk(top_down=True):
        # Prune subdirectories in place: drop .git and ignored dirs so walk()
        # does not descend into them.
        kept_dirs = []
        for name in dirnames:
            if name == ".git":
                continue
            child = dirpath / name
            rel = _relposix(root, child)
            if _is_ignored(spec, rel, is_dir=True):
                continue
            _reject_link(child, rel)
            kept_dirs.append(name)
        # Mutate the list in place so pathlib_next.walk honors the pruning.
        dirnames[:] = kept_dirs

        emitted_file_here = False
        for name in sorted(filenames):
            child = dirpath / name
            rel = _relposix(root, child)
            if _is_ignored(spec, rel, is_dir=False):
                continue
            _reject_link(child, rel)
            if not child.is_file():
                raise ValueError(
                    f"archive path {rel!r} is not a regular file; "
                    "special files are not supported"
                )
            emitted_file_here = True
            yield rel, child, False

        # If this directory kept no files and (after pruning) no subdirs, it is
        # an explicitly-empty directory worth recording. The tree root itself
        # (rel == ".") is never recorded as an entry.
        if not emitted_file_here and not dirnames:
            rel = _relposix(root, dirpath)
            if rel != ".":
                yield rel, dirpath, True


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _encode_record(
    rec_type: int,
    relposix: str,
    mode: int,
    mtime: float,
    content: bytes,
    metadata: str,
) -> bytes:
    """Encode one record using the selected version-2 metadata profile."""
    path_bytes = relposix.encode("utf-8")
    parts = [_REC_PREFIX.pack(rec_type, len(path_bytes)), path_bytes]
    if metadata == "none":
        parts.append(_CONTENT_LEN.pack(len(content)))
    else:
        try:
            millis = int(round(float(mtime) * 1000.0))
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError(
                f"mtime for {relposix!r} cannot be represented as milliseconds"
            ) from exc
        if not -(1 << 63) <= millis <= (1 << 63) - 1:
            raise ValueError(
                f"mtime for {relposix!r} is outside the signed millisecond range"
            )
        parts.append(
            _REC_META_BASIC.pack(int(mode) & 0o7777, millis, len(content))
        )
    if content:
        parts.append(content)
    return b"".join(parts)


def _validate_metadata(metadata: str) -> str:
    if metadata not in METADATA_PROFILES:
        valid = ", ".join(METADATA_PROFILES)
        raise ValueError(f"unknown metadata profile {metadata!r}; choose {valid}")
    return metadata


def archive_tree(
    root: PathLike,
    *,
    use_ignore: bool = True,
    extra_ignore: _ty.Optional[_ty.Sequence[str]] = None,
    metadata: str = "none",
) -> bytes:
    """Serialize the tree at ``root`` into one deterministic archive-stream ``bytes``.

    ``root`` may be a ``pathlib_next.Path``, a ``str``, or any ``os.PathLike``
    (coerced). The tree is walked with ``root.walk()``; the ignore filter and
    ``.git`` pruning follow the module docstring. Output is byte-for-byte
    deterministic for a given tree (records sorted by relpath).

    ``metadata`` is ``"none"`` by default and may be ``"basic"`` to capture
    ordinary permission bits and mtime. Compression is *not* applied here — the
    caller compresses the whole result via :func:`compress`.
    """
    metadata = _validate_metadata(metadata)
    root = _validate_root(root)

    entries = sorted(
        _walk_entries(root, use_ignore=use_ignore, extra_ignore=extra_ignore),
        key=lambda item: item[0],
    )

    records: list[bytes] = []
    for relposix, path, is_empty_dir in entries:
        if is_empty_dir:
            mode, mtime = 0, 0.0
            if metadata == "basic":
                try:
                    st = path.stat()
                    mode, mtime = st.st_mode, st.st_mtime
                except OSError:
                    pass
            records.append(
                _encode_record(
                    REC_EMPTY_DIR, relposix, mode, mtime, b"", metadata
                )
            )
        else:
            content = path.read_bytes()
            mode, mtime = 0, 0.0
            if metadata == "basic":
                try:
                    st = path.stat()
                    mode, mtime = st.st_mode, st.st_mtime
                except OSError:
                    pass
            records.append(
                _encode_record(REC_FILE, relposix, mode, mtime, content, metadata)
            )

    out = [
        MAGIC,
        _HEADER_V2.pack(
            FORMAT_VERSION, _METADATA_FLAGS[metadata], len(records)
        ),
    ]
    out.extend(records)
    return b"".join(out)


def list_paths(
    root: PathLike,
    *,
    use_ignore: bool = True,
    extra_ignore: _ty.Optional[_ty.Sequence[str]] = None,
) -> list[str]:
    """Return the sorted POSIX relpaths that :func:`archive_tree` would archive.

    Reuses the same walk + ignore logic, so the manifest :mod:`glyphive.layout` prints in the
    page header exactly matches the archived records. Empty-directory entries
    appear with a trailing ``/`` to distinguish them from files.
    """
    root = _validate_root(root)
    paths: list[str] = []
    for relposix, _path, is_empty_dir in _walk_entries(
        root, use_ignore=use_ignore, extra_ignore=extra_ignore
    ):
        paths.append(relposix + "/" if is_empty_dir else relposix)
    paths.sort()
    return paths


# ---------------------------------------------------------------------------
# Parsing (inverse split point — restore.unarchive reuses this)
# ---------------------------------------------------------------------------


def _parse_stream_header(data: bytes) -> _ty.Tuple[memoryview, int, int, str, int]:
    """Return ``(view, offset, version, profile, record_count)``."""
    mv = memoryview(data)
    if len(mv) < len(MAGIC) + 1:
        raise ValueError("archive stream too short for header")
    if bytes(mv[: len(MAGIC)]) != MAGIC:
        raise ValueError("bad archive magic (not a glyphive stream)")

    off = len(MAGIC)
    version = mv[off]
    if version == V1_FORMAT_VERSION:
        if len(mv) < len(MAGIC) + _HEADER_V1.size:
            raise ValueError("archive stream too short for version 1 header")
        _version, rec_count = _HEADER_V1.unpack_from(mv, off)
        return mv, off + _HEADER_V1.size, version, "basic", rec_count

    if version == FORMAT_VERSION:
        if len(mv) < len(MAGIC) + _HEADER_V2.size:
            raise ValueError("archive stream too short for version 2 header")
        _version, flags, rec_count = _HEADER_V2.unpack_from(mv, off)
        if flags not in (0, 1):
            raise ValueError(f"unknown archive metadata flags 0x{flags:02x}")
        profile = "basic" if flags else "none"
        return mv, off + _HEADER_V2.size, version, profile, rec_count

    raise ValueError(
        f"unsupported archive format version {version} "
        f"(this build handles {V1_FORMAT_VERSION} and {FORMAT_VERSION})"
    )


def stream_metadata(data: bytes) -> ArchiveMetadata:
    """Parse and return the archive version and explicit metadata profile.

    Version 1 is reported as ``metadata='basic'`` because its historical wire
    format always carried mode and mtime fields.
    """
    _mv, _off, version, profile, _count = _parse_stream_header(data)
    return ArchiveMetadata(version, profile)


def iter_records(data: bytes) -> _ty.Iterator[Record]:
    """Parse an archive stream, yielding :class:`Record` in stream order.

    Raises :class:`ValueError` on a truncated stream, unknown version/profile,
    unknown record type, or trailing bytes. The full stream is validated by
    restore before any records are materialized on disk.
    """
    mv, off, version, profile, rec_count = _parse_stream_header(data)

    for index in range(rec_count):
        if off + _REC_PREFIX.size > len(mv):
            raise ValueError(f"truncated record {index} (prefix)")
        rec_type, path_len = _REC_PREFIX.unpack_from(mv, off)
        off += _REC_PREFIX.size
        if rec_type not in (REC_FILE, REC_EMPTY_DIR):
            raise ValueError(f"unknown record type {rec_type} at index {index}")

        if off + path_len > len(mv):
            raise ValueError(f"truncated record {index} (path)")
        try:
            relposix = bytes(mv[off : off + path_len]).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"record {index} path is not valid UTF-8") from exc
        off += path_len

        if version == V1_FORMAT_VERSION:
            field_size = _REC_META_V1.size
            if off + field_size > len(mv):
                raise ValueError(f"truncated record {index} (metadata)")
            mode, mtime, content_len = _REC_META_V1.unpack_from(mv, off)
            off += field_size
        elif profile == "basic":
            field_size = _REC_META_BASIC.size
            if off + field_size > len(mv):
                raise ValueError(f"truncated record {index} (metadata)")
            mode, millis, content_len = _REC_META_BASIC.unpack_from(mv, off)
            mtime = millis / 1000.0
            off += field_size
        else:
            field_size = _CONTENT_LEN.size
            if off + field_size > len(mv):
                raise ValueError(f"truncated record {index} (content length)")
            mode, mtime, content_len = 0, 0.0, _CONTENT_LEN.unpack_from(mv, off)[0]
            off += field_size

        if off + content_len > len(mv):
            raise ValueError(f"truncated record {index} (content)")
        if rec_type == REC_EMPTY_DIR and content_len:
            raise ValueError(
                f"empty-directory record {index} has nonzero content length"
            )
        content = bytes(mv[off : off + content_len])
        off += content_len

        yield Record(rec_type, relposix, mode, mtime, content)

    if off != len(mv):
        raise ValueError(
            f"archive stream has {len(mv) - off} trailing byte(s) after records"
        )
