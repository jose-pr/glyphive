"""Explicit, deterministic discovery of trusted installed glyphive plugins.

Discovery loads third-party Python code in this process.  It is deliberately
opt-in, cached, and never runs during a normal glyphive import.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import re
import typing as _ty

from .codec import Codec
from .compression import CompressionMethod
from .render import RenderFormat
from .restore.ocr import OcrProvider

_GROUPS: _ty.Final = {
    "glyphive.codecs": Codec,
    "glyphive.compression": CompressionMethod,
    "glyphive.render_formats": RenderFormat,
    "glyphive.ocr_providers": OcrProvider,
}
_NAME = re.compile(r"[a-z][a-z0-9_-]*\Z")
_report: _ty.Optional["DiscoveryReport"] = None


@dataclass(frozen=True)
class PluginEntry:
    """Identity of one installed entry point."""

    group: str
    name: str
    distribution: str


@dataclass(frozen=True)
class PluginError:
    """A candidate that could not be registered."""

    entry: PluginEntry
    message: str


@dataclass(frozen=True)
class DiscoveryReport:
    """Complete immutable result of one process-wide discovery pass."""

    loaded: _ty.Tuple[PluginEntry, ...]
    errors: _ty.Tuple[PluginError, ...]


def _distribution_name(entry_point: _ty.Any) -> str:
    try:
        dist = entry_point.dist
        return str(dist.name or dist.metadata.get("Name") or "")
    except Exception:
        return ""


def _entry_points() -> _ty.List[_ty.Any]:
    found = metadata.entry_points()
    if hasattr(found, "select"):
        return [entry for group in _GROUPS for entry in found.select(group=group)]
    return [entry for group in _GROUPS for entry in found.get(group, ())]


def _snapshots() -> _ty.Dict[_ty.Type[_ty.Any], _ty.Dict[str, _ty.Type[_ty.Any]]]:
    return {base: dict(base._registry) for base in _GROUPS.values()}


def _restore(snapshots: _ty.Dict[_ty.Type[_ty.Any], _ty.Dict[str, _ty.Type[_ty.Any]]]) -> None:
    for base, snapshot in snapshots.items():
        base._registry.clear()
        base._registry.update(snapshot)


def discover() -> DiscoveryReport:
    """Load and validate installed entry points once, returning all diagnostics."""
    global _report
    if _report is not None:
        return _report

    candidates = sorted(
        _entry_points(),
        key=lambda entry: (entry.group, entry.name, _distribution_name(entry)),
    )
    loaded: _ty.List[PluginEntry] = []
    errors: _ty.List[PluginError] = []
    for candidate in candidates:
        identity = PluginEntry(
            candidate.group, candidate.name, _distribution_name(candidate)
        )
        base = _GROUPS[candidate.group]
        snapshots = _snapshots()
        try:
            implementation = candidate.load()
        except Exception as exc:
            _restore(snapshots)
            errors.append(PluginError(identity, f"load failed: {exc}"))
            continue

        # Class definition normally self-registers through __init_subclass__.
        # Undo all load-time registry effects so validation is the only commit.
        _restore(snapshots)
        try:
            if not isinstance(implementation, type) or not issubclass(
                implementation, base
            ):
                raise TypeError(f"object is not a {base.__name__} subclass")
            if not _NAME.fullmatch(candidate.name):
                raise ValueError("entry-point name is not a lowercase ASCII identifier")
            if getattr(implementation, "name", None) != candidate.name:
                raise ValueError(
                    f"class name {getattr(implementation, 'name', None)!r} "
                    f"does not match entry-point name {candidate.name!r}"
                )
            base._register_external(candidate.name, implementation)
        except Exception as exc:
            errors.append(PluginError(identity, f"registration failed: {exc}"))
            continue
        loaded.append(identity)

    _report = DiscoveryReport(tuple(loaded), tuple(errors))
    return _report


def _reset_for_tests() -> None:
    """Remove discovered classes and clear the one-shot cache (tests only)."""
    global _report
    for base in _GROUPS.values():
        base._reset_external()
    _report = None


__all__ = [
    "DiscoveryReport",
    "PluginEntry",
    "PluginError",
    "discover",
]
