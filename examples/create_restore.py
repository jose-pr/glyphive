"""Create, inspect, and restore one Glyphive text archive.

Run after installing the project:

    python examples/create_restore.py SOURCE BACKUP.txt RESTORED
"""

from __future__ import annotations

import sys
import typing as _ty

from glyphive.cli import run
from pathlib_next import Path


def main(argv: _ty.Optional[_ty.Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print(
            "usage: create_restore.py SOURCE BACKUP.txt RESTORED",
            file=sys.stderr,
        )
        return 2

    source, document, restored = map(Path, args)
    if not source.is_dir():
        print(f"source is not a directory: {source}", file=sys.stderr)
        return 2

    run(
        [
            "create",
            "-f",
            str(document),
            "--format",
            "text",
            "--compression",
            "gzip",
            "-C",
            str(source),
            ".",
        ]
    )
    run(["list", "-f", str(document)])
    return run(["extract", "-f", str(document), "-C", str(restored)])


if __name__ == "__main__":
    raise SystemExit(main())
