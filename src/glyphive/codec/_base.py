"""Typed codec registry used by the printable archive protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
import typing as _ty


class Codec(ABC):
    """Base class for named byte-to-lines codecs.

    Concrete subclasses register themselves at class definition time. Registry
    classes must be no-argument constructible; a future factory contract can
    relax that restriction without complicating this registry.
    """

    _registry: _ty.ClassVar[_ty.Dict[str, _ty.Type["Codec"]]] = {}
    _external: _ty.ClassVar[_ty.Set[str]] = set()
    name: _ty.ClassVar[str]

    def __init_subclass__(cls, **kwargs: _ty.Any) -> None:
        super().__init_subclass__(**kwargs)
        if any(
            getattr(getattr(cls, method, None), "__isabstractmethod__", False)
            for method in ("encode", "decode")
        ):
            return
        name = getattr(cls, "name", None)
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ValueError(
                f"codec {cls.__module__}.{cls.__qualname__} must define a valid "
                "lowercase ASCII name"
            )
        if name in Codec._registry:
            existing = Codec._registry[name]
            raise ValueError(
                f"duplicate codec name {name!r}: "
                f"{existing.__module__}.{existing.__qualname__} and "
                f"{cls.__module__}.{cls.__qualname__}"
            )
        Codec._registry[name] = cls

    @classmethod
    def names(cls) -> _ty.List[str]:
        """Return all registered codec names in stable order."""
        return sorted(Codec._registry)

    @classmethod
    def available(cls) -> _ty.List[str]:
        """Return names whose implementation reports itself available."""
        return sorted(
            name
            for name, implementation in Codec._registry.items()
            if implementation.is_available()
        )

    @classmethod
    def get(cls, name: str) -> "Codec":
        """Return a fresh no-argument codec or raise an actionable error."""
        try:
            implementation = Codec._registry[name]
        except (KeyError, TypeError):
            valid = ", ".join(Codec.names()) or "(none)"
            raise ValueError(
                f"unknown codec {name!r}; available codecs: {valid}"
            ) from None
        return implementation()

    @classmethod
    def is_available(cls) -> bool:
        """Return whether this implementation can be selected."""
        return True

    @classmethod
    def _register_external(cls, name: str, implementation: _ty.Type["Codec"]) -> None:
        if name in Codec._registry:
            raise ValueError(f"duplicate codec name {name!r}")
        Codec._registry[name] = implementation
        Codec._external.add(name)

    @classmethod
    def _discard_implementation(cls, implementation: _ty.Type["Codec"]) -> None:
        for name, registered in list(Codec._registry.items()):
            if registered is implementation:
                Codec._registry.pop(name)
                Codec._external.discard(name)

    @classmethod
    def _reset_external(cls) -> None:
        for name in Codec._external:
            Codec._registry.pop(name, None)
        Codec._external.clear()

    @abstractmethod
    def encode(self, data: bytes, **options: _ty.Any) -> _ty.List[str]:
        """Encode bytes as printable lines."""

    @abstractmethod
    def decode(self, lines: _ty.Iterable[str], **options: _ty.Any) -> bytes:
        """Decode printable lines as bytes."""
