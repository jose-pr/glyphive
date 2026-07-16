"""Typed registry contract for physical document renderers."""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
import typing as _ty

from glyphive.layout import Page

DEFAULT_MONO_FONT = "Consolas"
DEFAULT_DOCX_FONT = DEFAULT_MONO_FONT
DEFAULT_PDF_FONT = "Courier"


class RenderFormat(ABC):
    """Base class for stateless, no-argument render formats."""

    _registry: _ty.ClassVar[_ty.Dict[str, _ty.Type["RenderFormat"]]] = {}
    name: _ty.ClassVar[str]

    def __init_subclass__(cls, **kwargs: _ty.Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(getattr(cls, "render", None), "__isabstractmethod__", False):
            return
        name = getattr(cls, "name", None)
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ValueError(f"render format {cls.__qualname__} has an invalid name")
        if name in RenderFormat._registry:
            existing = RenderFormat._registry[name]
            raise ValueError(
                f"duplicate render format name {name!r}: "
                f"{existing.__module__}.{existing.__qualname__} and "
                f"{cls.__module__}.{cls.__qualname__}"
            )
        RenderFormat._registry[name] = cls

    @classmethod
    def names(cls) -> _ty.List[str]:
        return sorted(RenderFormat._registry)

    @classmethod
    def available(cls) -> _ty.List[str]:
        return sorted(
            name
            for name, implementation in RenderFormat._registry.items()
            if implementation.is_available()
        )

    @classmethod
    def get(cls, name: str) -> "RenderFormat":
        try:
            implementation = RenderFormat._registry[name]
        except (KeyError, TypeError):
            valid = ", ".join(RenderFormat.names()) or "(none)"
            raise ValueError(
                f"unknown render format {name!r}; available formats: {valid}"
            ) from None
        return implementation()

    @classmethod
    def is_available(cls) -> bool:
        return True

    @abstractmethod
    def render(
        self,
        pages: _ty.List[Page],
        out: _ty.Union[str, "_os.PathLike[str]"],
        *,
        font: _ty.Optional[str] = None,
        font_size: float = 11.0,
    ) -> None:
        """Render already-paginated pages."""
