"""Typed registry contract for archive compression methods."""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
import typing as _ty


class CompressionMethod(ABC):
    """Base class for stateless, no-argument compression implementations."""

    _registry: _ty.ClassVar[_ty.Dict[str, _ty.Type["CompressionMethod"]]] = {}
    _external: _ty.ClassVar[_ty.Set[str]] = set()
    name: _ty.ClassVar[str]

    def __init_subclass__(cls, **kwargs: _ty.Any) -> None:
        super().__init_subclass__(**kwargs)
        if any(
            getattr(getattr(cls, method, None), "__isabstractmethod__", False)
            for method in ("compress", "decompress")
        ):
            return
        name = getattr(cls, "name", None)
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ValueError(
                f"compression {cls.__module__}.{cls.__qualname__} must define a "
                "valid lowercase ASCII name"
            )
        if name in CompressionMethod._registry:
            existing = CompressionMethod._registry[name]
            raise ValueError(
                f"duplicate compression name {name!r}: "
                f"{existing.__module__}.{existing.__qualname__} and "
                f"{cls.__module__}.{cls.__qualname__}"
            )
        CompressionMethod._registry[name] = cls

    @classmethod
    def names(cls) -> _ty.List[str]:
        """Return all registered method names in stable order."""
        return sorted(CompressionMethod._registry)

    @classmethod
    def available(cls) -> _ty.List[str]:
        """Return method names whose optional backend is available."""
        return sorted(
            name
            for name, implementation in CompressionMethod._registry.items()
            if implementation.is_available()
        )

    @classmethod
    def get(cls, name: str) -> "CompressionMethod":
        """Return a fresh no-argument method by wire name."""
        try:
            implementation = CompressionMethod._registry[name]
        except (KeyError, TypeError):
            valid = ", ".join(CompressionMethod.names()) or "(none)"
            raise ValueError(
                f"unknown compression method {name!r}; available methods: {valid}"
            ) from None
        return implementation()

    @classmethod
    def is_available(cls) -> bool:
        """Return whether this method can be selected without an extra."""
        return True

    @classmethod
    def _register_external(cls, name: str, implementation: _ty.Type["CompressionMethod"]) -> None:
        if name in CompressionMethod._registry:
            raise ValueError(f"duplicate compression name {name!r}")
        CompressionMethod._registry[name] = implementation
        CompressionMethod._external.add(name)

    @classmethod
    def _discard_implementation(cls, implementation: _ty.Type["CompressionMethod"]) -> None:
        for name, registered in list(CompressionMethod._registry.items()):
            if registered is implementation:
                CompressionMethod._registry.pop(name)
                CompressionMethod._external.discard(name)

    @classmethod
    def _reset_external(cls) -> None:
        for name in CompressionMethod._external:
            CompressionMethod._registry.pop(name, None)
        CompressionMethod._external.clear()

    @abstractmethod
    def compress(self, data: bytes, level: _ty.Optional[int] = None) -> bytes:
        """Compress one whole archive stream."""

    @abstractmethod
    def decompress(self, data: bytes) -> bytes:
        """Decompress one whole archive stream."""
