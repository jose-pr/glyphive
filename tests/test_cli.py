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

    # Corrupt the display-only prose header. list must verify/decode first and
    # print the authoritative protected metadata, not the damaged summary.
    text = archive_file.read_text(encoding="utf-8")
    archive_file.write_text(
        text.replace("codec=base16c-crc16-rs", "codec=base16c-crl", 1), encoding="utf-8"
    )

    # list returns 0 and prints verified protected metadata.
    capsys.readouterr()  # clear
    rc = cli.run(["list", "-f", str(archive_file)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "glyphive" in captured.out
    assert "codec=base16c-crc16-rs" in captured.out
    assert "files=" in captured.out
    assert "meta=none" in captured.out


def test_create_with_parity_pages_survives_deleted_page_blocks(tmp_path):
    """``--parity-pages 2``: delete 2 whole page blocks, extract restores exactly."""
    from glyphive.render.formats.text import FORM_FEED

    src = _make_srcdir(tmp_path)
    # Enough incompressible content that pagination yields several data pages
    # even after (zstd/gzip) compression.
    (src / "d.bin").write_bytes(os.urandom(20000))
    archive_file = tmp_path / "a.txt"
    outdir = tmp_path / "out"

    rc = cli.run(
        [
            "create",
            "-f", str(archive_file),
            "-C", str(src),
            "--parity-pages", "2",
            "--compression", "none",
            ".",
        ]
    )
    assert rc == 0

    text = archive_file.read_text(encoding="utf-8")
    blocks = text.split(FORM_FEED)
    header_line = blocks[0].splitlines()[0]
    assert "pgpar=2" in header_line

    # Delete 2 whole page blocks (not page 1, which carries the header) --
    # this exercises the same whole-page-loss recovery path as the layout
    # tests, but through the full CLI create -> extract flow.
    assert len(blocks) >= 5  # at least: header page + >=2 data + >=2 parity
    victims = {1, 2}  # blocks[0] is page 1; drop pages 2 and 3
    surviving_blocks = [b for i, b in enumerate(blocks) if i not in victims]
    archive_file.write_text(FORM_FEED.join(surviving_blocks), encoding="utf-8")

    rc = cli.run(["extract", "-f", str(archive_file), "-C", str(outdir)])
    assert rc == 0
    _compare_dirs(src, outdir)


def test_create_rejects_parity_pages_exceeding_the_255_page_cap(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"

    with pytest.raises(SystemExit, match="255"):
        cli.run(
            [
                "create",
                "-f", str(archive_file),
                "-C", str(src),
                "--parity-pages", "255",
                ".",
            ]
        )


def test_tar_style_mode_flags_roundtrip(tmp_path, capsys):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "tar-style.txt"
    outdir = tmp_path / "out"

    assert cli.run(["-c", "-f", str(archive_file), "-C", str(src), "."]) == 0
    assert cli.run(["-x", "-f", str(archive_file), "-C", str(outdir)]) == 0
    _compare_dirs(src, outdir)

    capsys.readouterr()
    assert cli.run(["-t", "-f", str(archive_file)]) == 0
    assert "codec=base16c-crc16-rs" in capsys.readouterr().out


def test_create_and_extract_log_progressive_stage_events(tmp_path, caplog):
    """create/extract log progress events, not just a final one-line summary.

    Phase 4 of bounded_memory_streaming_pipeline.md requires successful runs
    to "report progressive staging and publication" -- verify both commands
    actually emit intermediate stage events via the logger, not only the
    pre-existing final "wrote"/"restored" line.
    """
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "progress.txt"
    outdir = tmp_path / "out"

    with caplog.at_level("INFO", logger="create"):
        assert cli.run(["create", "-f", str(archive_file), "-C", str(src), "."]) == 0
    create_messages = [r.getMessage() for r in caplog.records if r.name == "create"]
    assert any(m.startswith("archived ") for m in create_messages)
    assert any(m.startswith("compressed ") for m in create_messages)
    assert any(m.startswith("encoded ") for m in create_messages)
    assert any(m.startswith("rendered ") for m in create_messages)

    caplog.clear()
    with caplog.at_level("INFO", logger="extract"):
        assert cli.run(["extract", "-f", str(archive_file), "-C", str(outdir)]) == 0
    extract_messages = [r.getMessage() for r in caplog.records if r.name == "extract"]
    assert any(m.startswith("staged ") for m in extract_messages)
    assert any(m.startswith("published ") for m in extract_messages)


def test_create_uses_configured_spool_directory_and_chunk_size(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "streamed.txt"
    spool_dir = tmp_path / "spools"
    spool_dir.mkdir()

    assert cli.run(
        [
            "create",
            "-f",
            str(archive_file),
            "-C",
            str(src),
            "--none",
            "--temp-dir",
            str(spool_dir),
            "--chunk-size",
            "17",
            ".",
        ]
    ) == 0
    assert list(spool_dir.iterdir()) == []
    assert cli.run(["extract", "-f", str(archive_file), "-C", str(tmp_path / "out")]) == 0
    _compare_dirs(src, tmp_path / "out")


def test_plugins_flag_discovers_before_selector_validation(tmp_path, monkeypatch):
    from glyphive import plugins
    from glyphive.cli import create as create_command

    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "plugin.txt"

    class PluginCodec(codec.Codec):
        name = "plugin_codec"

        def encode(self, data, **options):
            return codec.Base16CCodec().encode(data, **options)

        def decode(self, lines, **options):
            return codec.Base16CCodec().decode(lines, **options)

    codec.Codec._discard_implementation(PluginCodec)
    report = plugins.DiscoveryReport(
        (plugins.PluginEntry("glyphive.codecs", "plugin_codec", "test"),), ()
    )

    def discover():
        codec.Codec._register_external("plugin_codec", PluginCodec)
        return report

    monkeypatch.setattr(plugins, "discover", discover)
    try:
        assert cli.run(
            [
                "--plugins",
                "create",
                "-f",
                str(archive_file),
                "-C",
                str(src),
                "--codec",
                "plugin_codec",
                ".",
            ]
        ) == 0
        assert "codec=plugin_codec" in archive_file.read_text(encoding="utf-8")
    finally:
        codec.Codec._reset_external()


def test_plugins_flag_surfaces_nonfatal_diagnostics(monkeypatch, capsys):
    from glyphive import plugins

    error = plugins.PluginError(
        plugins.PluginEntry("glyphive.codecs", "bad", "broken-dist"),
        "load failed: boom",
    )
    monkeypatch.setattr(
        plugins, "discover", lambda: plugins.DiscoveryReport((), (error,))
    )
    assert cli._discover_plugins(["--plugins", "create"]) == ["create"]
    assert "warning: plugin glyphive.codecs:bad from broken-dist" in capsys.readouterr().err


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


def test_create_explicit_line_width_controls_g1_rows(tmp_path):
    src = _make_srcdir(tmp_path)
    (src / "wide.bin").write_bytes(bytes(range(256)) * 8)
    archive_file = tmp_path / "wide.txt"

    assert cli.run(
        [
            "create", "-f", str(archive_file), "-C", str(src),
            "--compression", "none", "--line-width", "100", ".",
        ]
    ) == 0

    payloads = [
        line.split()[1]
        for line in archive_file.read_text(encoding="utf-8").splitlines()
        if line.startswith(("L", "P"))
    ]
    assert max(map(len, payloads)) == 100


def test_create_rejects_too_small_line_width(tmp_path):
    src = _make_srcdir(tmp_path)
    with pytest.raises(SystemExit, match="line-width must be at least 2"):
        cli.run(
            ["create", "-f", str(tmp_path / "bad.txt"), "-C", str(src),
             "--line-width", "1", "."]
        )


def test_generic_codec_and_compression_selectors_roundtrip(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "custom.txt"
    outdir = tmp_path / "out"
    saved_codecs = dict(codec.Codec._registry)
    saved_compressions = dict(compression.CompressionMethod._registry)

    class TestCodec(codec.Codec):
        name = "test_codec"

        def encode(self, data, **options):
            return codec.Base16CCodec().encode(data, **options)

        def decode(self, lines, **options):
            return codec.Base16CCodec().decode(lines, **options)

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


def test_output_format_is_inferred_from_destination_suffix(tmp_path, monkeypatch):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "inferred.PDF"
    from glyphive.cli import create as create_command

    monkeypatch.setattr(create_command._render, "available", lambda: ["text", "docx"])
    with pytest.raises(ValueError, match=r"install glyphive\[pdf\]"):
        cli.run(["create", "-f", str(archive_file), "-C", str(src), "."])
    assert not archive_file.exists()


@pytest.mark.parametrize(
    ("destination", "expected"),
    [
        ("archive.docx", "docx"),
        ("archive.txt", "text"),
        ("archive.text", "text"),
        ("archive", "text"),
        ("archive.unknown", "text"),
    ],
)
def test_output_format_suffix_mapping_and_fallback(destination, expected):
    from glyphive.cli.create import _output_format

    assert _output_format(destination, None) == expected


def test_explicit_output_format_wins_over_destination_suffix(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "actually-text.pdf"

    assert cli.run(
        [
            "create",
            "-f",
            str(archive_file),
            "-C",
            str(src),
            "--format",
            "text",
            "--compression",
            "none",
            ".",
        ]
    ) == 0
    assert archive_file.read_text(encoding="utf-8").startswith("#!glyphive")


def test_pdf_suffix_remains_ordinary_pdf_without_explicit_qr_format():
    from glyphive.cli.create import _output_format

    assert _output_format("archive.pdf", None) == "pdf"
    assert _output_format("archive.pdf", "qr") == "qr"
    assert _output_format("archive.pdf", "hybrid") == "hybrid"


def test_explicit_ocr_engine_is_forwarded_for_image_input(tmp_path, monkeypatch):
    from glyphive.cli import extract as extract_command
    from glyphive.restore import unarchive

    seen = {}
    monkeypatch.setattr(
        extract_command,
        "load_image_lines",
        lambda source, engine=None, blur=0.0: seen.update(source=source, engine=engine) or [],
    )
    monkeypatch.setattr(
        unarchive,
        "restore_document_spooled",
        lambda lines, dest, **options: ({}, []),
    )

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


def test_extract_from_qr_uses_qr_loader(tmp_path, monkeypatch):
    from glyphive.cli import extract as extract_command
    from glyphive.restore import unarchive

    seen = {}
    monkeypatch.setattr(
        extract_command,
        "load_qr_lines",
        lambda source: seen.update(source=source) or ["qr-transcript"],
    )
    monkeypatch.setattr(
        unarchive,
        "restore_document_spooled",
        lambda lines, dest, **options: seen.update(lines=list(lines)) or ({}, []),
    )

    assert cli.run(
        [
            "extract",
            "-f",
            str(tmp_path / "qr-pages"),
            "--from-qr",
            "-C",
            str(tmp_path / "out"),
        ]
    ) == 0
    assert seen["source"] == tmp_path / "qr-pages"
    assert seen["lines"] == ["qr-transcript"]


def test_extract_rejects_conflicting_image_modes(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        cli.run(
            [
                "extract",
                "-f",
                str(tmp_path / "scan.png"),
                "--from-images",
                "--from-qr",
                "-C",
                str(tmp_path / "out"),
            ]
        )


def test_transcript_directory_is_read_in_sorted_nonrecursive_order(tmp_path):
    from glyphive.cli._common import load_transcript_lines

    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    (transcripts / "b.txt").write_text("second\n", encoding="utf-8")
    (transcripts / "a.txt").write_text("first\fpage\n", encoding="utf-8")
    nested = transcripts / "nested"
    nested.mkdir()
    (nested / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    assert load_transcript_lines(transcripts) == ["first", "page", "second"]


def test_image_directory_uses_one_provider_and_sorted_files(tmp_path, monkeypatch):
    from glyphive.cli._common import load_image_lines
    from glyphive.restore import ocr

    images = tmp_path / "images"
    images.mkdir()
    (images / "b.png").write_bytes(b"b")
    (images / "a.png").write_bytes(b"a")
    (images / "nested").mkdir()
    seen = {}

    def fake_ocr_pages(paths, *, engine=None):
        seen["paths"] = [path.name for path in paths]
        seen["engine"] = engine
        return [["page-a"], ["page-b", "tail"]]

    monkeypatch.setattr(ocr, "ocr_pages", fake_ocr_pages)
    assert load_image_lines(images, engine="mock") == ["page-a", "page-b", "tail"]
    assert seen == {"paths": ["a.png", "b.png"], "engine": "mock"}


@pytest.mark.parametrize("loader_name", ["load_transcript_lines", "load_image_lines"])
def test_empty_input_directory_fails_clearly(tmp_path, loader_name):
    from glyphive.cli import _common

    empty = tmp_path / "empty"
    empty.mkdir()
    loader = getattr(_common, loader_name)
    with pytest.raises(ValueError, match="input directory contains no files"):
        loader(empty)


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
