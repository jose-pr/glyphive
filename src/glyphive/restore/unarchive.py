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
import shutil as _shutil
import tempfile as _tempfile
import typing as _ty

from pathlib_next import Path

from .. import archive
from .decode import RestoreError

__all__ = [
    "RestoreError",
    "unarchive_bytes",
    "restore_document",
    "restore_document_spooled",
    "unarchive_spool",
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
    if dest_path.exists() and not dest_path.is_dir():
        raise RestoreError(f"destination {dest_path!s} exists and is not a directory")
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


def _same_file(left: "Path", right: "Path", chunk_size: int) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            left_chunk = left_stream.read(chunk_size)
            right_chunk = right_stream.read(chunk_size)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def unarchive_spool(
    raw_source: _ty.BinaryIO,
    dest: DestLike,
    *,
    overwrite: bool = False,
    chunk_size: int = 1024 * 1024,
    max_file_bytes: _ty.Optional[int] = None,
) -> _ty.List[str]:
    """Stage streamed archive records privately, then publish after validation."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    dest_path = _coerce_dest(dest)
    dest_parent = dest_path.parent
    dest_parent.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_path.resolve()
    stage = Path(_tempfile.mkdtemp(prefix=f".{dest_path.name}.glyphive-", dir=str(dest_parent)))
    stage_resolved = stage.resolve()
    staged: _ty.List[_ty.Tuple[archive.RecordHeader, "Path", "Path"]] = []
    seen: _ty.Set[str] = set()
    current_header = None
    current_stream = None
    current_stage = None
    try:
        raw_source.seek(0)
        prefix = raw_source.read(len(archive.MAGIC) + 6)
        stream_meta = archive.stream_metadata(prefix)
        apply_metadata = stream_meta.metadata == "basic"
        raw_source.seek(0)
        for event in archive.iter_record_events(
            raw_source,
            chunk_size=chunk_size,
            max_content_bytes=max_file_bytes,
        ):
            if isinstance(event, archive.RecordHeader):
                if current_stream is not None:
                    current_stream.close()
                    current_stream = None
                    _restore_metadata(
                        current_stage,
                        current_header.mode,
                        current_header.mtime,
                        enabled=apply_metadata,
                    )
                final_target = _safe_target(dest_resolved, event.path)
                stage_target = _safe_target(stage_resolved, event.path)
                target_key = _os.path.normcase(str(stage_target.resolve()))
                if target_key in seen:
                    raise RestoreError(f"duplicate archive path {event.path!r}")
                seen.add(target_key)
                staged.append((event, stage_target, final_target))
                current_header, current_stage = event, stage_target
                try:
                    if event.type == archive.REC_EMPTY_DIR:
                        stage_target.mkdir(parents=True, exist_ok=True)
                        _restore_metadata(
                            stage_target,
                            event.mode,
                            event.mtime,
                            enabled=apply_metadata,
                        )
                    else:
                        stage_target.parent.mkdir(parents=True, exist_ok=True)
                        current_stream = stage_target.open("wb")
                except OSError as exc:
                    raise RestoreError(
                        f"archive path {event.path!r} conflicts with another record"
                    ) from exc
            else:
                if current_stream is None:
                    raise RestoreError("archive content chunk has no file record")
                current_stream.write(event.data)
        if current_stream is not None:
            current_stream.close()
            current_stream = None
            _restore_metadata(
                current_stage,
                current_header.mode,
                current_header.mtime,
                enabled=apply_metadata,
            )

        # Preflight every final collision before final-path mutation.
        identical: _ty.Set[str] = set()
        for header, staged_target, final_target in staged:
            ancestor = final_target.parent
            while ancestor != dest_resolved and dest_resolved in ancestor.parents:
                if ancestor.exists() and not ancestor.is_dir():
                    raise RestoreError(
                        f"parent of target {header.path!r} is not a directory"
                    )
                ancestor = ancestor.parent
            if not final_target.exists():
                continue
            if header.type == archive.REC_EMPTY_DIR:
                if not final_target.is_dir():
                    raise RestoreError(f"target {header.path!r} is not a directory")
                continue
            if final_target.is_dir():
                raise RestoreError(f"target {header.path!r} is a directory")
            if not overwrite:
                if _same_file(staged_target, final_target, chunk_size):
                    identical.add(header.path)
                else:
                    raise RestoreError(
                        f"target {header.path!r} already exists with different "
                        "content; refusing to overwrite"
                    )

        if not dest_path.exists():
            stage.replace(dest_path)
            stage = None
        else:
            for header, staged_target, final_target in staged:
                if header.type == archive.REC_EMPTY_DIR:
                    final_target.mkdir(parents=True, exist_ok=True)
                elif header.path not in identical:
                    final_target.parent.mkdir(parents=True, exist_ok=True)
                    staged_target.replace(final_target)
                _restore_metadata(
                    final_target,
                    header.mode,
                    header.mtime,
                    enabled=apply_metadata,
                )
        return [header.path for header, _staged, _final in staged]
    finally:
        if current_stream is not None:
            current_stream.close()
        if stage is not None and stage.exists():
            _shutil.rmtree(str(stage), ignore_errors=True)


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
    _meta, written = restore_document_spooled(
        text_lines, dest, overwrite=overwrite
    )
    return written


def restore_document_spooled(
    text_lines: _ty.Iterable[str],
    dest: DestLike,
    *,
    overwrite: bool = False,
    temp_dir: _ty.Optional[str] = None,
    chunk_size: int = 1024 * 1024,
    max_output_bytes: _ty.Optional[int] = None,
) -> _ty.Tuple[_ty.Dict[str, _ty.Any], _ty.List[str]]:
    """Decode to a private spool, validate globally, stage, then publish."""
    from .decode import decode_document_to_spool

    with _tempfile.TemporaryFile(dir=temp_dir) as raw_spool:
        meta = decode_document_to_spool(
            text_lines,
            raw_spool,
            max_output_bytes=max_output_bytes,
            chunk_size=chunk_size,
            temp_dir=temp_dir,
        )
        written = unarchive_spool(
            raw_spool,
            dest,
            overwrite=overwrite,
            chunk_size=chunk_size,
            max_file_bytes=max_output_bytes or int(meta["bytes"]),
        )
    return meta, written
