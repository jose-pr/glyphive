"""glyphive command-line interface package."""

from __future__ import annotations

import sys
from builtins import list as _list

import duho
from duho import LoggingArgs

from .create import Create
from .extract import Extract
from .info import Info
from .inspect import Inspect
from .list import List
from .train import Train

__all__ = ["Create", "Extract", "Glyphive", "Info", "Inspect", "List", "run"]


_MODE_FLAGS = {"-c": "create", "-x": "extract", "-t": "list"}


def _expand_mode_flag(argv):
    """Translate a leading tar-style mode flag into a subcommand."""
    args = _list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in _MODE_FLAGS:
        args[0] = _MODE_FLAGS[args[0]]
    return args


def _discover_plugins(args):
    """Consume the global opt-in flag and report non-fatal plugin failures."""
    enabled = "--plugins" in args
    args = [arg for arg in args if arg != "--plugins"]
    if enabled:
        from ..plugins import discover

        report = discover()
        for error in report.errors:
            entry = error.entry
            source = f" from {entry.distribution}" if entry.distribution else ""
            print(
                f"warning: plugin {entry.group}:{entry.name}{source}: "
                f"{error.message}",
                file=sys.stderr,
            )
    return args


class Glyphive(LoggingArgs):
    """Archive trees to printable pages and restore them.

    Create selections use the named registries: ``--codec``, ``--compression``,
    ``--format``, and ``--metadata``. Extract image input accepts
    ``--ocr-engine``; legacy compression aliases remain supported. A leading
    tar-style ``-c``, ``-x``, or ``-t`` may replace the corresponding command.
    """

    _version_ = duho.AUTO
    _subcommands_ = [Create, Extract, Info, Inspect, List, Train]

    plugins: bool = False
    "Discover trusted installed glyphive plugins before dispatch."
    ("--plugins",)


def run(argv=None) -> int:
    """Console-script / ``python -m glyphive`` entry point."""
    return duho.main(Glyphive, _discover_plugins(_expand_mode_flag(argv)))
