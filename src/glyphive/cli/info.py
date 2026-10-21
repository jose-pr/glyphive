"""The ``glyphive info`` command — report what this install can actually do.

Every named registry (codec, compression, render, OCR) already exposes
registered-vs-available names programmatically; this command is the CLI
surface for that, plus a font-lookup helper, so a user does not need to
import the package and query four registries by hand to answer "what can
this install of glyphive do right now."
"""

from __future__ import annotations

import typing as _ty

from duho import LoggingArgs, OutputArgs

__all__ = ["Info"]


class Info(OutputArgs, LoggingArgs):
    """List available codecs, compression methods, render formats, OCR
    engines, and (optionally) a font's resolvability."""

    _parsername_ = "info"

    font: "_ty.Optional[str]" = None
    "Check whether this font name would resolve for PDF output (core, "
    "bundled, or an OS-font-store match by filename or family name)."
    ("--font",)

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
        self._output_(report, text=self._render_text)

    @staticmethod
    def _render_text(report: "_ty.Dict[str, _ty.Any]") -> str:
        lines: "_ty.List[str]" = []
        out = lines.append

        def format_entries(section: "_ty.Dict[str, _ty.Any]") -> str:
            parts = []
            for entry in section["entries"]:
                label = entry["name"]
                if entry["name"] == section.get("default"):
                    label += " (default)"
                label += " (available)" if entry["available"] else " (NOT available)"
                parts.append(label)
            return ", ".join(parts) if parts else "(none registered)"

        out(f"codecs:      {format_entries(report['codecs'])}")
        out(f"compression: {format_entries(report['compression'])}")
        out(f"render:      {format_entries(report['render_formats'])}")
        ocr_section = report["ocr_engines"]
        out(f"ocr:         {format_entries(ocr_section)}")
        if ocr_section.get("preferred_order"):
            out(
                f"  preferred order (first available wins): "
                f"{', '.join(ocr_section['preferred_order'])}"
            )
        fonts = report["fonts"]
        out(f"fonts:       core: {', '.join(fonts['core'])}")
        out(f"             bundled: {', '.join(fonts['bundled'])}")
        out(f"             {fonts['system_lookup']}")
        if "font_lookup" in report:
            lookup = report["font_lookup"]
            if lookup["resolved"]:
                where = f" ({lookup['kind']}" + (
                    f": {lookup['path']}" if "path" in lookup else ""
                ) + ")"
                out(f"font check:  {lookup['requested']!r} resolves{where}")
            else:
                out(
                    f"font check:  {lookup['requested']!r} NOT found "
                    "(not core, not bundled, not in the OS font stores)"
                )
        return "\n".join(lines)
