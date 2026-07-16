"""Focused tests for the self-contained pyz builder."""

from pathlib import Path
import subprocess
import zipfile

import pytest

import package


def test_vendor_scope_contains_required_dependencies_only():
    assert package.RUNTIME_DEPENDENCIES == (
        "pathlib_next",
        "duho>=0.2.0",
        "pathspec",
        "reedsolo",
    )
    assert not any(
        any(optional.lower() in dependency.lower() for optional in package._OPTIONAL_DEPENDENCY_NAMES)
        for dependency in package.RUNTIME_DEPENDENCIES
    )
    assert package.OPTIONAL_EXTRA_DEPENDENCIES["all"] == (
        "fpdf2",
        "python-docx",
        "zstandard",
        "Pillow",
        "pytesseract",
    )


def test_builder_stages_runtime_files_and_strips_build_noise(tmp_path, monkeypatch):
    commands = []

    def fake_pip(command, check):
        assert check is True
        commands.append(command)
        stage = Path(command[command.index("--target") + 1])
        (stage / "required_dep.py").write_text("value = 1\n", encoding="utf-8")
        (stage / "tests").mkdir()
        (stage / "tests" / "test_dep.py").write_text("", encoding="utf-8")
        (stage / "required_dep.dist-info").mkdir()
        (stage / "required_dep.dist-info" / "METADATA").write_text("", encoding="utf-8")
        (stage / "__pycache__").mkdir()
        (stage / "__pycache__" / "required_dep.pyc").write_bytes(b"cache")

    monkeypatch.setattr(package.subprocess, "run", fake_pip)
    output = package.build_pyz(tmp_path / "nested" / "glyphive.pyz", "python-custom")

    assert output.exists()
    target_index = commands[0].index("--target")
    assert commands[0][target_index + 2 :] == list(package.RUNTIME_DEPENDENCIES)
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "__main__.py" in names
    assert "glyphive/cli/__init__.py" in names
    assert "required_dep.py" in names
    assert not any("tests/" in name or name.endswith(".dist-info/METADATA") for name in names)
    assert not any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names)
    assert output.read_bytes().startswith(b"#!python-custom\n")


def test_builder_reports_pip_failures(tmp_path, monkeypatch, capsys):
    def failed_pip(command, check):
        raise subprocess.CalledProcessError(23, command)

    monkeypatch.setattr(package.subprocess, "run", failed_pip)

    assert package.main(["--out", str(tmp_path / "glyphive.pyz")]) == 1
    error = capsys.readouterr().err
    assert "pip failed while vendoring" in error
    assert "exit code 23" in error
    assert "--target" in error


def test_builder_rejects_optional_vendor_edits(monkeypatch):
    monkeypatch.setattr(package, "RUNTIME_DEPENDENCIES", ("zstandard",))
    with pytest.raises(package.BuildError, match="optional dependencies"):
        package._vendor_dependencies(Path("unused-stage"))


def test_universal_build_rejects_native_files(tmp_path, monkeypatch):
    def fake_pip(command, check):
        stage = Path(command[command.index("--target") + 1])
        (stage / "native.pyd").write_bytes(b"native")

    monkeypatch.setattr(package.subprocess, "run", fake_pip)
    with pytest.raises(package.BuildError, match="universal pyz cannot contain native"):
        package.build_pyz(tmp_path / "glyphive.pyz")


def test_optional_build_can_contain_native_files(tmp_path, monkeypatch):
    commands = []

    def fake_pip(command, check):
        commands.append(command)
        stage = Path(command[command.index("--target") + 1])
        (stage / "native.pyd").write_bytes(b"native")

    monkeypatch.setattr(package.subprocess, "run", fake_pip)
    output = package.build_pyz(
        tmp_path / "glyphive-windows-all.pyz",
        extras=["all"],
    )

    assert output.exists()
    target_index = commands[0].index("--target")
    assert commands[0][target_index + 2 :] == list(
        package.RUNTIME_DEPENDENCIES + package.OPTIONAL_EXTRA_DEPENDENCIES["all"]
    )


def test_optional_build_cannot_use_universal_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(package.subprocess, "run", lambda command, check: None)
    with pytest.raises(package.BuildError, match="OS-specific output name"):
        package.build_pyz(tmp_path / "glyphive.pyz", extras=["all"])


def test_source_date_epoch_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "not-a-timestamp")
    with pytest.raises(package.BuildError, match="SOURCE_DATE_EPOCH"):
        package._source_date_epoch()


def test_cli_surface_uses_duho_fields():
    assert package.Package.out == str(package.DEFAULT_OUTPUT)
    assert package.Package.interpreter == package.DEFAULT_INTERPRETER
    assert package.Package.extras == []
    with pytest.raises(SystemExit) as excinfo:
        package.main(["--help"])
    assert excinfo.value.code == 0


def test_duho_cli_accepts_repeated_extras(monkeypatch, tmp_path):
    received = {}

    def fake_build(output, interpreter, *, extras):
        received.update(output=output, interpreter=interpreter, extras=extras)
        return Path(output)

    monkeypatch.setattr(package, "build_pyz", fake_build)
    output = tmp_path / "glyphive-linux-all.pyz"
    assert package.main(
        ["--extras", "all", "--extras", "zstd", "--out", str(output)]
    ) == 0
    assert received["extras"] == ["all", "zstd"]
