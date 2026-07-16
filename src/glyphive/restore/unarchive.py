"""glyphive — archive bytes → a directory tree on disk (the inverse of archive.py).

This module is the write side of restore. It takes the verified archive byte
stream from :func:`glyphive.restore.decode.decode_document`, parses it with the
existing :func:`glyphive.archive.iter_records` (it does **not** re-implement the
wire format), and materializes each record under a destination directory using
``pathlib_next.Path`` for every filesystem operation.

Two safety rails enforcing "no silent
corruption / no silent partial restore":

- **Path-traversal safety.** A record whose relpath is absolute, or whose
  resolved location escapes the destination via ``..``, is *rejected* with a
  :class:`~glyphive.restore.decode.RestoreError` naming the offending path.
  Nothing is written outside ``dest``.
- **No silent clobber.** With ``overwrite=False`` (the default), an existing
  target file whose bytes differ from the record is a hard error rather than a
  silent overwrite; an identical existing file is left as-is (idempotent). With
  ``overwrite=True`` existing files are replaced.

Mode and mtime are restored best-effort only when the archive profile carries
them: v1 streams and v2 ``basic`` streams. On Windows most permission bits are
no-ops, and a failure to set either is swallowed (wrapped in ``try/except``) —
content correctness is the contract, metadata is a courtesy.
"""

from __future__ import annotations

import os as _os
import typing as _ty

from pathlib_next import Path

from .. import archive
from .decode import RestoreError, decode_document

__all__ = [
    "RestoreError",
    "unarchive_bytes",
    "restore_document",
]

# Types accepted for a destination: a pathlib_next Path, a str, or os.PathLike.
DestLike = _ty.Union[str, "Path", "_os.PathLike[str]"]


def _coerce_dest(dest: DestLike) -> "Path":
    """Coerce ``str`` / ``os.PathLike`` / ``Path`` into a ``pathlib_next.Path``."""
    if isinstance(dest, Path):
        return dest
    return Path(dest)


def _safe_target(dest_resolved: "Path", relpath: str) -> "Path":
    """Resolve ``relpath`` under ``dest`` or raise on any traversal escape.

    Rejects an absolute relpath outright, then joins it under ``dest`` and
    verifies the *resolved* result stays inside the resolved ``dest``. This
    catches ``..`` components that would climb out of the destination (whether
    or not the intermediate directories exist yet). Raises :class:`RestoreError`
    naming the offending path; never returns a path outside ``dest``.
    """
    # Normalize separators the archive uses POSIX "/"; a backslash in a record
    # written on Windows must also be treated as a separator when we check for
    # traversal so "..\\evil" cannot slip past.
    normalized = relpath.replace("\\", "/")

    if not normalized or normalized in (".", "./"):
        raise RestoreError(
            f"record has an empty or dot relpath {relpath!r}; refusing to write"
        )

    candidate = Path(normalized)
    if candidate.is_absolute():
        raise RestoreError(
            f"record path {relpath!r} is absolute; refusing to write outside "
            "the destination directory"
        )

    target = dest_resolved.joinpath(*normalized.split("/"))
    resolved = target.resolve()

    # The resolved target must be dest itself or live strictly beneath it.
    if resolved != dest_resolved and dest_resolved not in resolved.parents:
        raise RestoreError(
            f"record path {relpath!r} escapes the destination directory "
            f"(resolves to {resolved!s}); refusing path-traversal write"
        )
    return target


def _restore_metadata(
    target: "Path", mode: int, mtime: float, *, enabled: bool
) -> None:
    """Best-effort restore of carried mode + mtime. Never raises."""
    if not enabled:
        return
    try:
        target.chmod(mode & 0o7777)
    except (OSError, NotImplementedError, ValueError):
        pass  # most bits are no-ops on Windows; metadata is a courtesy
    try:
        _os.utime(_os.fspath(target), (mtime, mtime))
    except (OSError, ValueError, OverflowError):
        pass


def _record_targets(
    dest_resolved: "Path", records: _ty.Sequence[archive.Record]
) -> _ty.List[_ty.Tuple[archive.Record, "Path"]]:
    """Validate every record path before any destination mutation."""
    return [
        (record, _safe_target(dest_resolved, record.path)) for record in records
    ]


def unarchive_bytes(
    raw: bytes,
    dest: DestLike,
    *,
    overwrite: bool = False,
) -> _ty.List[str]:
    """Write the archive byte stream ``raw`` into ``dest``; return relpaths written.

    The archive header and all records are validated before the destination is
    created. Metadata is applied only when the stream explicitly carries it.
    """
    dest_path = _coerce_dest(dest)
    stream_meta = archive.stream_metadata(raw)
    records = list(archive.iter_records(raw))
    dest_resolved = dest_path.resolve()
    targets = _record_targets(dest_resolved, records)
    apply_metadata = stream_meta.metadata == "basic"
    dest_path.mkdir(parents=True, exist_ok=True)

    written: _ty.List[str] = []

    for record, target in targets:
        if record.type == archive.REC_EMPTY_DIR:
            target.mkdir(parents=True, exist_ok=True)
            _restore_metadata(
                target, record.mode, record.mtime, enabled=apply_metadata
            )
            written.append(record.path)
            continue

        # Ensure the parent directory chain exists.
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and not overwrite:
            # Idempotent if identical; otherwise refuse to silently destroy.
            try:
                existing = target.read_bytes()
            except OSError as exc:
                raise RestoreError(
                    f"cannot verify existing target {record.path!r} before "
                    f"writing (overwrite=False): {exc}"
                ) from exc
            if existing != record.content:
                raise RestoreError(
                    f"target {record.path!r} already exists with different "
                    "content; refusing to overwrite (pass overwrite=True to "
                    "replace)"
                )
            # Identical content already present: count it as written, skip I/O.
            _restore_metadata(
                target, record.mode, record.mtime, enabled=apply_metadata
            )
            written.append(record.path)
            continue

        target.write_bytes(record.content)
        _restore_metadata(target, record.mode, record.mtime, enabled=apply_metadata)
        written.append(record.path)

    return written


def restore_document(
    text_lines: _ty.Iterable[str],
    dest: DestLike,
    *,
    overwrite: bool = False,
) -> _ty.List[str]:
    """Full text-transcript → tree convenience: decode then unarchive.

    Equivalent to :func:`glyphive.restore.decode.decode_document` followed by
    :func:`unarchive_bytes`. This is the single call the CLI uses for
    ``extract`` from a text transcript. All the integrity guarantees of both
    stages apply: a missing page, an unrecoverable line, a whole-document hash
    mismatch, or a path-traversal record each raises loudly and nothing corrupt
    is written.

    Returns the list of relpaths written under ``dest``.
    """
    _meta, raw = decode_document(text_lines)
    return unarchive_bytes(raw, dest, overwrite=overwrite)
