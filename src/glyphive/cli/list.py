"""The ``glyphive list`` command."""

from __future__ import annotations

from duho import LoggingArgs
from pathlib_next import Path

from .. import archive as _archive
from .. import layout as _layout
from ._common import load_transcript_lines

__all__ = ["List"]


class List(LoggingArgs):
    """Print a document's header and file manifest without extracting."""

    _parsername_ = "list"
    _parseraliases_ = ["t"]

    file: str
    "Input document (text transcript)."
    ("-f", "--file")

    def __call__(self) -> int:
        from ..restore import decode as _decode

        lines = load_transcript_lines(Path(self.file))
        header = _layout.parse_header(
            next(line for line in lines if line.startswith("#!glyphive"))
        )
        profile = header.get("meta")
        profile_token = f" meta={profile}" if profile is not None else ""
        print(
            "glyphive v{v} codec={codec} comp={comp}{profile} files={files} "
            "bytes={bytes} pages={pages}".format(
                **header, profile=profile_token
            )
        )
        # Decode before enumerating so list also verifies document integrity.
        _meta, raw = _decode.decode_document(lines)
        for record in _archive.iter_records(raw):
            kind = "d" if record.type == _archive.REC_EMPTY_DIR else "f"
            print(f"{kind} {record.path}")
        return 0
