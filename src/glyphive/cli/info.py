"""The ``glyphive info`` command — report what this install can actually do.

Every named registry (codec, compression, render, OCR) already exposes
registered-vs-available names programmatically; this command is the CLI
surface for that, plus a font-lookup helper, so a user does not need to
import the package and query four registries by hand to answer "what can
this install of glyphive do right now."
"""

from __future__ import annotations

import json as _json
import typing as _ty

from duho import LoggingArgs

__all__ = ["Info"]


class Info(LoggingArgs):
    """List available codecs, compression methods, render formats, OCR
    engines, and (optionally) a font's resolvability."""

    _parsername_ = "info"

    font: "_ty.Optional[str]" = None
    "Check whether this font name would resolve for PDF output (core, "
    "bundled, or an OS-font-store match by filename or family name)."
    ("--font",)

    json: bool = False
    "Emit the report as a machine-readable JSON object instead of text."
    ("--json",)

    def __call__(self) -> int:
        report = self._build_report()
        self._emit(report)
        return 0

    def _build_report(self) -> "_ty.Dict[str, _ty.Any]":
        from .. import codec as _codec
        from .. import compression as _compression
        from .. import render as _render
        from ..restore import ocr as _ocr

        report: "_ty.Dict[str, _ty.Any]" = {
            "codecs": self._registry_section(
                _codec.names(), _codec.available(), default="base16g-crc16-rs"
            ),
            "compression": self._registry_section(
                _compression.names(),
                _compression.available(),
                default=_compression.default(),
            ),
            "render_formats": self._registry_section(
                _render.names(), _render.available()
            ),
            "ocr_engines": self._registry_section(
                _ocr.names(), _ocr.available(), preferred=_ocr.available_engines()
            ),
            "fonts": self._fonts_section(),
        }
        if self.font is not None:
            report["font_lookup"] = self._font_lookup(self.font)
        return report

    @staticmethod
    def _registry_section(
        names: "_ty.List[str]",
        available: "_ty.List[str]",
        *,
        default: "_ty.Optional[str]" = None,
        preferred: "_ty.Optional[_ty.List[str]]" = None,
    ) -> "_ty.Dict[str, _ty.Any]":
        available_set = set(available)
        entries = [
            {"name": name, "available": name in available_set} for name in names
        ]
        section: "_ty.Dict[str, _ty.Any]" = {"entries": entries}
        if default is not None:
            section["default"] = default
        if preferred is not None:
            section["preferred_order"] = preferred
        return section

    @staticmethod
    def _fonts_section() -> "_ty.Dict[str, _ty.Any]":
        from ..render.formats.pdf import _BUNDLED_FONTS, _CORE_FONTS

        return {
            "core": sorted(_CORE_FONTS),
            "bundled": sorted(_BUNDLED_FONTS),
            "system_lookup": "enabled (pass any installed font's name or "
            "filename; use --font to check a specific one)",
        }

    @staticmethod
    def _font_lookup(font: str) -> "_ty.Dict[str, _ty.Any]":
        from ..render.formats.pdf import _BUNDLED_FONTS, _CORE_FONTS, _find_system_font

        lowered = font.lower()
        if lowered in _CORE_FONTS:
            return {"requested": font, "resolved": True, "kind": "core"}
        if lowered in _BUNDLED_FONTS:
            return {"requested": font, "resolved": True, "kind": "bundled"}
        found = _find_system_font(font)
        if found is not None:
            return {
                "requested": font,
                "resolved": True,
                "kind": "system",
                "path": str(found),
            }
        return {"requested": font, "resolved": False}

    def _emit(self, report: "_ty.Dict[str, _ty.Any]") -> None:
        if self.json:
            print(_json.dumps(report, indent=2, default=str))
            return

        def format_entries(section: "_ty.Dict[str, _ty.Any]") -> str:
            parts = []
            for entry in section["entries"]:
                label = entry["name"]
                if entry["name"] == section.get("default"):
                    label += " (default)"
                label += " (available)" if entry["available"] else " (NOT available)"
                parts.append(label)
            return ", ".join(parts) if parts else "(none registered)"

        print(f"codecs:      {format_entries(report['codecs'])}")
        print(f"compression: {format_entries(report['compression'])}")
        print(f"render:      {format_entries(report['render_formats'])}")
        ocr_section = report["ocr_engines"]
        print(f"ocr:         {format_entries(ocr_section)}")
        if ocr_section.get("preferred_order"):
            print(f"  preferred order (first available wins): "
                  f"{', '.join(ocr_section['preferred_order'])}")
        fonts = report["fonts"]
        print(f"fonts:       core: {', '.join(fonts['core'])}")
        print(f"             bundled: {', '.join(fonts['bundled'])}")
        print(f"             {fonts['system_lookup']}")
        if "font_lookup" in report:
            lookup = report["font_lookup"]
            if lookup["resolved"]:
                where = f" ({lookup['kind']}" + (
                    f": {lookup['path']}" if "path" in lookup else ""
                ) + ")"
                print(f"font check:  {lookup['requested']!r} resolves{where}")
            else:
                print(
                    f"font check:  {lookup['requested']!r} NOT found "
                    "(not core, not bundled, not in the OS font stores)"
                )
