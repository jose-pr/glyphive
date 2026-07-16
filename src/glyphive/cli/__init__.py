"""glyphive command-line interface package."""

from __future__ import annotations

import duho
from duho import LoggingArgs

from .create import Create
from .extract import Extract
from .list import List

__all__ = ["Create", "Extract", "Glyphive", "List", "run"]


class Glyphive(LoggingArgs):
    """Archive trees to printable pages and restore them.

    Create selections use the named registries: ``--codec``, ``--compression``,
    ``--format``, and ``--metadata``. Extract image input accepts
    ``--ocr-engine``; legacy compression aliases remain supported.
    """

    _version_ = duho.AUTO
    _subcommands_ = [Create, Extract, List]


def run(argv=None) -> int:
    """Console-script / ``python -m glyphive`` entry point."""
    return duho.main(Glyphive, argv)
