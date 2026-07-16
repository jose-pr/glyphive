"""Tests for the glyphive CLI, driven in-process via ``glyphive.cli.run``.

``run(argv)`` returns an int exit code and takes an argv list, so we call it
directly (faster and cleaner than a subprocess, and avoids interpreter-path
issues on this machine).
"""

import os

import pytest

from glyphive import cli
from glyphive import codec, compression


def _make_srcdir(tmp_path):
    src = tmp_path / "srcdir"
    src.mkdir()
    (src / "a.txt").write_text("alpha\n", encoding="utf-8")
    (src / "b.txt").write_text("beta beta\n", encoding="utf-8")
    sub = src / "nested"
    sub.mkdir()
    (sub / "c.bin").write_bytes(bytes(range(64)))
    return src


def _compare_dirs(src, dst):
    def snapshot(base):
        out = {}
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, base).replace(os.sep, "/")
                with open(full, "rb") as fh:
                    out[rel] = fh.read()
        return out

    assert snapshot(str(src)) == snapshot(str(dst))


def test_create_extract_list_roundtrip(tmp_path, capsys):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"
    outdir = tmp_path / "out"

    rc = cli.run(["create", "-f", str(archive_file), "-C", str(src), "."])
    assert rc == 0
    assert archive_file.exists()
    header_line = archive_file.read_text(encoding="utf-8").splitlines()[0]
    assert "meta=none" in header_line

    rc = cli.run(["extract", "-f", str(archive_file), "-C", str(outdir)])
    assert rc == 0
    _compare_dirs(src, outdir)

    # list returns 0 and prints the header line.
    capsys.readouterr()  # clear
    rc = cli.run(["list", "-f", str(archive_file)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "glyphive" in captured.out
    assert "files=" in captured.out
    assert "meta=none" in captured.out


def test_old_header_without_metadata_remains_readable(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"

    cli.run(["create", "-f", str(archive_file), "-C", str(src), "."])
    lines = archive_file.read_text(encoding="utf-8").splitlines()
    old_header = lines[0].replace(" meta=none", "")
    from glyphive import layout

    parsed = layout.parse_header(old_header)
    assert "meta" not in parsed


def test_metadata_basic_selector_is_recorded_and_restored(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "basic.txt"
    outdir = tmp_path / "out-basic"

    assert cli.run(
        [
            "create",
            "-f",
            str(archive_file),
            "-C",
            str(src),
            "--metadata",
            "basic",
            "--compression",
            "none",
            ".",
        ]
    ) == 0
    assert "meta=basic" in archive_file.read_text(encoding="utf-8").splitlines()[0]
    assert cli.run(
        ["extract", "-f", str(archive_file), "-C", str(outdir)]
    ) == 0
    _compare_dirs(src, outdir)


def test_generic_codec_and_compression_selectors_roundtrip(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "custom.txt"
    outdir = tmp_path / "out"
    saved_codecs = dict(codec.Codec._registry)
    saved_compressions = dict(compression.CompressionMethod._registry)

    class TestCodec(codec.Codec):
        name = "test_codec"

        def encode(self, data, **options):
            return codec.G1Codec().encode(data, **options)

        def decode(self, lines, **options):
            return codec.G1Codec().decode(lines, **options)

    class TestCompression(compression.CompressionMethod):
        name = "test_compression"

        def compress(self, data, level=None):
            return data

        def decompress(self, data):
            return data

    try:
        assert cli.run(
            [
                "create",
                "-f",
                str(archive_file),
                "-C",
                str(src),
                "--codec",
                "test_codec",
                "--compression",
                "test_compression",
                ".",
            ]
        ) == 0
        header = archive_file.read_text(encoding="utf-8").splitlines()[0]
        assert "codec=test_codec" in header
        assert "comp=test_compression" in header
        assert cli.run(
            ["extract", "-f", str(archive_file), "-C", str(outdir)]
        ) == 0
        _compare_dirs(src, outdir)
    finally:
        codec.Codec._registry.clear()
        codec.Codec._registry.update(saved_codecs)
        compression.CompressionMethod._registry.clear()
        compression.CompressionMethod._registry.update(saved_compressions)


def test_generic_and_legacy_compression_disagreement_rejected(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"
    with pytest.raises(SystemExit, match="disagrees"):
        cli.run(
            [
                "create",
                "-f",
                str(archive_file),
                "-C",
                str(src),
                "--compression",
                "none",
                "--gzip",
                ".",
            ]
        )
    assert not archive_file.exists()


def test_unknown_selectors_fail_before_writing(tmp_path):
    src = _make_srcdir(tmp_path)
    for option, value, message in (
        ("--codec", "missing", "registered codecs"),
        ("--compression", "missing", "registered compression methods"),
        ("--format", "missing", "registered render formats"),
    ):
        archive_file = tmp_path / (value + option[2:] + ".txt")
        with pytest.raises(ValueError, match=message):
            cli.run(
                [
                    "create",
                    "-f",
                    str(archive_file),
                    "-C",
                    str(src),
                    option,
                    value,
                    ".",
                ]
            )
        assert not archive_file.exists()


def test_unavailable_renderer_mentions_extra(tmp_path, monkeypatch):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.pdf"
    from glyphive.cli import create as create_command

    monkeypatch.setattr(create_command._render, "available", lambda: ["text", "docx"])
    with pytest.raises(ValueError, match=r"install glyphive\[pdf\]"):
        cli.run(
            [
                "create",
                "-f",
                str(archive_file),
                "-C",
                str(src),
                "--format",
                "pdf",
                ".",
            ]
        )
    assert not archive_file.exists()


def test_explicit_ocr_engine_is_forwarded_for_image_input(tmp_path, monkeypatch):
    from glyphive.cli import extract as extract_command
    from glyphive.restore import decode, unarchive

    seen = {}
    monkeypatch.setattr(
        extract_command,
        "load_image_lines",
        lambda source, engine=None: seen.update(source=source, engine=engine) or [],
    )
    monkeypatch.setattr(decode, "decode_document", lambda lines: ({}, b"raw"))
    monkeypatch.setattr(unarchive, "unarchive_bytes", lambda raw, dest, overwrite=False: [])

    assert cli.run(
        [
            "extract",
            "-f",
            str(tmp_path / "scan.png"),
            "--from-images",
            "--ocr-engine",
            "test-engine",
            "-C",
            str(tmp_path / "out"),
        ]
    ) == 0
    assert seen["engine"] == "test-engine"


def test_mutually_exclusive_compression_guard(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"
    with pytest.raises(SystemExit):
        cli.run(
            [
                "create",
                "-f",
                str(archive_file),
                "-C",
                str(src),
                "--gzip",
                "--zstd",
                ".",
            ]
        )


def test_multiple_paths_rejected(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"
    with pytest.raises(SystemExit):
        cli.run(
            [
                "create",
                "-f",
                str(archive_file),
                "-C",
                str(src),
                "a.txt",
                "b.txt",
            ]
        )
