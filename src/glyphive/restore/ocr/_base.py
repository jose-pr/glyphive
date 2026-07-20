"""Typed registry contract for optional OCR providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
import typing as _ty


class OcrLine(_ty.NamedTuple):
    """One OCR-read line: its text plus optional per-character confidence.

    ``char_conf`` is ``None`` when the provider (or a non-OCR text/QR path)
    has no per-character confidence to offer -- callers MUST keep tolerating
    that (plan 3: OCR-confidence-assisted char-level erasures is an
    optimization, never a requirement). When present, ``char_conf`` has
    exactly ``len(text)`` entries, one per character of ``text`` (spaces
    included -- a provider gives whitespace a confidence of ``1.0``), each
    in ``0.0..1.0`` (or ``None`` for a single character the provider itself
    could not score). It is deliberately RAW: aligned to the full printed
    line as read, not yet sliced down to the codec's payload region -- see
    :func:`glyphive.codec.base16c.align_payload_char_conf`, which does that
    alignment once the codec's frame shape is known.
    """

    text: str
    char_conf: _ty.Optional[_ty.List[_ty.Optional[float]]] = None


class OcrProvider(ABC):
    """Base class for stateless, no-argument image OCR providers."""

    _registry: _ty.ClassVar[_ty.Dict[str, _ty.Type["OcrProvider"]]] = {}
    _external: _ty.ClassVar[_ty.Set[str]] = set()
    name: _ty.ClassVar[str]

    def __init_subclass__(cls, **kwargs: _ty.Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(getattr(cls, "ocr_image", None), "__isabstractmethod__", False):
            return
        name = getattr(cls, "name", None)
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ValueError(f"OCR provider {cls.__qualname__} has an invalid name")
        if name in OcrProvider._registry:
            existing = OcrProvider._registry[name]
            raise ValueError(
                f"duplicate OCR provider name {name!r}: "
                f"{existing.__module__}.{existing.__qualname__} and "
                f"{cls.__module__}.{cls.__qualname__}"
            )
        OcrProvider._registry[name] = cls

    @classmethod
    def names(cls) -> _ty.List[str]:
        return sorted(OcrProvider._registry)

    @classmethod
    def available(cls) -> _ty.List[str]:
        return sorted(
            name
            for name, implementation in OcrProvider._registry.items()
            if implementation.is_available()
        )

    @classmethod
    def get(cls, name: str) -> "OcrProvider":
        try:
            implementation = OcrProvider._registry[name]
        except (KeyError, TypeError):
            valid = ", ".join(OcrProvider.names()) or "(none)"
            raise ValueError(
                f"unknown OCR engine {name!r}; available engines: {valid}"
            ) from None
        return implementation()

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def _register_external(cls, name: str, implementation: _ty.Type["OcrProvider"]) -> None:
        if name in OcrProvider._registry:
            raise ValueError(f"duplicate OCR provider name {name!r}")
        OcrProvider._registry[name] = implementation
        OcrProvider._external.add(name)

    @classmethod
    def _discard_implementation(cls, implementation: _ty.Type["OcrProvider"]) -> None:
        for name, registered in list(OcrProvider._registry.items()):
            if registered is implementation:
                OcrProvider._registry.pop(name)
                OcrProvider._external.discard(name)

    @classmethod
    def _reset_external(cls) -> None:
        for name in OcrProvider._external:
            OcrProvider._registry.pop(name, None)
        OcrProvider._external.clear()

    @abstractmethod
    def ocr_image(self, image_path: _ty.Any) -> _ty.List[OcrLine]:
        """Return candidate lines (text + optional per-char confidence) from one image."""
