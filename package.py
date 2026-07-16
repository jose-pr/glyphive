#!/usr/bin/env python3
"""Build Glyphive's self-contained zipapp.

Without ``--extras`` the pyz contains the required core dependencies only and
rejects bundled native extensions. Explicit OS-specific optional builds may
vendor native-capable packages and must therefore use an explicit non-universal
output filename.
"""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import typing as _ty
import zipapp

import duho
from duho import LoggingArgs


DEFAULT_OUTPUT = Path("dist") / "glyphive.pyz"
DEFAULT_INTERPRETER = "/usr/bin/env python3"
RUNTIME_DEPENDENCIES = (
    "pathlib_next",
    "duho>=0.2.0",
    "pathspec",
    "reedsolo",
)
OPTIONAL_EXTRA_DEPENDENCIES = {
    "pdf": ("fpdf2",),
    "docx": ("python-docx",),
    "zstd": ("zstandard",),
    "ocr": ("Pillow", "pytesseract"),
    "all": ("fpdf2", "python-docx", "zstandard", "Pillow", "pytesseract"),
}
_OPTIONAL_DEPENDENCY_NAMES = frozenset(
    dependency
    for dependencies in OPTIONAL_EXTRA_DEPENDENCIES.values()
    for dependency in dependencies
)
_UNNEEDED_DIRECTORY_NAMES = frozenset({"__pycache__", "tests"})
_UNNEEDED_SUFFIXES = (".dist-info", ".egg-info")
_NATIVE_SUFFIXES = (".pyd", ".dll", ".dylib")
_MIN_ZIP_TIMESTAMP = 315619200  # 1980-01-02, safe across local-time conversion.


class BuildError(RuntimeError):
    """A user-facing pyz build failure."""


def _command_text(command: list[str]) -> str:
    return shlex.join(command)


def _selected_extra_dependencies(extras: Iterable[str]) -> tuple[str, ...]:
    selected = []
    unknown = []
    for extra in extras:
        if extra not in OPTIONAL_EXTRA_DEPENDENCIES:
            unknown.append(extra)
        else:
            selected.extend(OPTIONAL_EXTRA_DEPENDENCIES[extra])
    if unknown:
        choices = ", ".join(sorted(OPTIONAL_EXTRA_DEPENDENCIES))
        raise BuildError(f"unknown optional extra(s): {', '.join(unknown)}; choose from {choices}")
    return tuple(dict.fromkeys(selected))


def _vendor_dependencies(stage: Path, extras: Iterable[str] = ()) -> None:
    optional_dependencies = _selected_extra_dependencies(extras)
    optional = [
        dependency
        for dependency in RUNTIME_DEPENDENCIES
        if any(name.lower() in dependency.lower() for name in _OPTIONAL_DEPENDENCY_NAMES)
    ]
    if optional:
        raise BuildError(
            "optional dependencies cannot be bundled in the universal pyz: "
            + ", ".join(optional)
        )

    dependencies = RUNTIME_DEPENDENCIES + optional_dependencies
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--no-compile",
        "--upgrade",
        "--target",
        str(stage),
        *dependencies,
    ]
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise BuildError(
            "pip could not be started while building the pyz: "
            f"{_command_text(command)} ({exc})"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise BuildError(
            "pip failed while vendoring required runtime dependencies "
            f"(exit code {exc.returncode}): {_command_text(command)}"
        ) from exc


def _copy_package(stage: Path, source: Path) -> None:
    if not source.is_dir():
        raise BuildError(f"Glyphive package source was not found: {source}")
    shutil.copytree(
        source,
        stage / "glyphive",
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.py[cod]", "tests", "*.dist-info", "*.egg-info"
        ),
    )


def _remove_unneeded_files(stage: Path) -> None:
    for path in sorted(stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and (
            path.name in _UNNEEDED_DIRECTORY_NAMES
            or path.name.endswith(_UNNEEDED_SUFFIXES)
        ):
            shutil.rmtree(path)
        elif path.is_file() and (path.suffix in {".pyc", ".pyo"} or path.name.endswith(_UNNEEDED_SUFFIXES)):
            path.unlink()


def _source_date_epoch() -> int:
    value = os.environ.get("SOURCE_DATE_EPOCH", "")
    if not value:
        return _MIN_ZIP_TIMESTAMP
    try:
        return max(int(value), _MIN_ZIP_TIMESTAMP)
    except ValueError as exc:
        raise BuildError("SOURCE_DATE_EPOCH must be an integer Unix timestamp") from exc


def _normalize_mtimes(stage: Path, timestamp: int) -> None:
    for path in stage.rglob("*"):
        if path.is_file():
            os.utime(path, (timestamp, timestamp))


def _native_files(stage: Path) -> list[Path]:
    native = []
    for path in stage.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.endswith(_NATIVE_SUFFIXES) or ".so" in name:
            native.append(path)
    return native


def build_pyz(
    output: _ty.Union[Path, str] = DEFAULT_OUTPUT,
    interpreter: str = DEFAULT_INTERPRETER,
    *,
    source_root: _ty.Optional[_ty.Union[Path, str]] = None,
    extras: Iterable[str] = (),
) -> Path:
    """Vendor selected dependencies and create a runnable zipapp."""
    root = Path(source_root) if source_root is not None else Path(__file__).resolve().parent
    package_source = root / "src" / "glyphive"
    output_path = Path(output)
    selected_extras = tuple(extras)
    _selected_extra_dependencies(selected_extras)
    if selected_extras and output_path.name == DEFAULT_OUTPUT.name:
        raise BuildError(
            "optional pyz builds must use an OS-specific output name, not glyphive.pyz"
        )

    with tempfile.TemporaryDirectory(prefix="glyphive-pyz-") as temporary:
        stage = Path(temporary) / "stage"
        stage.mkdir()
        try:
            _vendor_dependencies(stage, selected_extras)
            _copy_package(stage, package_source)
            (stage / "__main__.py").write_text(
                "from glyphive.cli import run\n\nraise SystemExit(run())\n",
                encoding="utf-8",
            )
            _remove_unneeded_files(stage)
            native = _native_files(stage)
            if native and not selected_extras:
                names = ", ".join(str(path.relative_to(stage)) for path in native[:3])
                if len(native) > 3:
                    names += ", ..."
                raise BuildError(
                    "universal pyz cannot contain native extension files; "
                    f"found {names}. Build an explicit OS-specific optional "
                    "artifact instead."
                )
            _normalize_mtimes(stage, _source_date_epoch())
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except BuildError:
            raise
        except OSError as exc:
            raise BuildError(f"pyz staging failed: {exc}") from exc
        try:
            zipapp.create_archive(
                str(stage),
                target=str(output_path),
                interpreter=interpreter,
                compressed=True,
            )
        except Exception as exc:
            raise BuildError(f"zipapp failed while creating {output_path}: {exc}") from exc

    return output_path


class Package(LoggingArgs):
    """Build the Glyphive self-contained pyz."""

    out: str = str(DEFAULT_OUTPUT)
    "Output pyz path (default: dist/glyphive.pyz)."
    ("--out",)

    interpreter: str = DEFAULT_INTERPRETER
    "Interpreter/shebang for the pyz (default: /usr/bin/env python3)."
    ("--python",)

    extras: list[str] = []
    "Declared optional extra to vendor; repeat --extras for multiple extras."
    ("--extras",)

    def __call__(self) -> int:
        try:
            output = build_pyz(self.out, self.interpreter, extras=self.extras)
        except BuildError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        self._logger_.info("built %s", output)
        return 0


def main(argv: _ty.Optional[_ty.List[str]] = None) -> int:
    return duho.main(Package, argv)


if __name__ == "__main__":
    raise SystemExit(main())
