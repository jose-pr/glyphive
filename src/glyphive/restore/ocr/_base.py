"""Typed registry contract for optional OCR providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
import typing as _ty


class OcrProvider(ABC):
    """Base class for stateless, no-argument image OCR providers."""

    _registry: _ty.ClassVar[_ty.Dict[str, _ty.Type["OcrProvider"]]] = {}
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

    @abstractmethod
    def ocr_image(self, image_path: _ty.Any) -> _ty.List[str]:
        """Return candidate text lines from one image."""
