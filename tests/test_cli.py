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
    # Compact display-only header: '#!glyphive v1 <codec>[,<comp>] files=.. bytes=.. pages=..'
    assert header_line.startswith("#!glyphive v1 base16g-crc16-rs")
    assert "files=" in header_line and "pages=" in header_line
    # sha256/meta are NOT in the human line anymore (they live in the H frames).
    assert "sha256=" not in header_line and "meta=" not in header_line

    rc = cli.run(["extract", "-f", str(archive_file), "-C", str(outdir)])
    assert rc == 0
    _compare_dirs(src, outdir)

    # Corrupt the display-only prose header. list must verify/decode first and
    # print the authoritative protected metadata, not the damaged summary.
    text = archive_file.read_text(encoding="utf-8")
    archive_file.write_text(
        text.replace("base16g-crc16-rs", "base16c-crl", 1), encoding="utf-8"
    )

    # list returns 0 and prints verified protected metadata.
    capsys.readouterr()  # clear
    rc = cli.run(["list", "-f", str(archive_file)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "glyphive" in captured.out
    assert "codec=base16g-crc16-rs" in captured.out
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


def test_create_no_longer_rejects_parity_pages_exceeding_the_old_255_page_cap(tmp_path):
    """Plan 5: page-parity switched to a GF(2^16) field past 255 total
    blocks, raising the cap to 65535 -- this used to be a create-time error.
    """
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"

    exit_code = cli.run(
        [
            "create",
            "-f", str(archive_file),
            "-C", str(src),
            "--parity-pages", "255",
            ".",
        ]
    )
    assert exit_code == 0
    assert archive_file.exists()


def test_create_with_gf216_parity_pages_survives_deleted_page_blocks(tmp_path):
    """Full create -> delete 5 data pages -> extract round trip driven past
    the GF(2^8) 255-block cap (300+ data pages), forcing the GF(2^16) page-
    parity field end to end through the real CLI + text renderer.
    """
    from glyphive.render.formats.text import FORM_FEED

    src = _make_srcdir(tmp_path)
    (src / "big.bin").write_bytes(os.urandom(450_000))  # ~300+ data pages
    archive_file = tmp_path / "a.txt"
    outdir = tmp_path / "out"

    rc = cli.run(
        [
            "create",
            "-f", str(archive_file),
            "-C", str(src),
            # Pin a stable page geometry (width=auto; font-size 10, distinct
            # from both the class default 11 and mode presets' 6/8, so the
            # explicit override actually takes -- see Create._apply_mode's
            # "still at class default" sentinel check) -- this test's whole
            # point is triggering the GF(2^16) 255-block threshold with a
            # specific page count, not exercising --mode.
            "--mode", "conservative",
            "--font-size", "10",
            "--parity-pages", "5",
            "--compression", "none",
            ".",
        ]
    )
    assert rc == 0

    text = archive_file.read_text(encoding="utf-8")
    blocks = text.split(FORM_FEED)
    header_line = blocks[0].splitlines()[0]
    assert "pgpar=5" in header_line

    data_pages = int(header_line.split("pages=", 1)[1].split()[0])
    assert data_pages + 5 > 255  # sanity: this is actually exercising GF(2^16)

    # Drop 5 whole data page blocks (never page 1, which carries the header).
    assert len(blocks) > 10
    victims = {1, 2, 3, 4, 5}
    surviving_blocks = [b for i, b in enumerate(blocks) if i not in victims]
    archive_file.write_text(FORM_FEED.join(surviving_blocks), encoding="utf-8")

    rc = cli.run(["extract", "-f", str(archive_file), "-C", str(outdir)])
    assert rc == 0
    _compare_dirs(src, outdir)


def test_tar_style_mode_flags_roundtrip(tmp_path, capsys):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "tar-style.txt"
    outdir = tmp_path / "out"

    assert cli.run(["-c", "-f", str(archive_file), "-C", str(src), "."]) == 0
    assert cli.run(["-x", "-f", str(archive_file), "-C", str(outdir)]) == 0
    _compare_dirs(src, outdir)

    capsys.readouterr()
    assert cli.run(["-t", "-f", str(archive_file)]) == 0
    assert "codec=base16g-crc16-rs" in capsys.readouterr().out


def test_create_and_extract_log_progressive_stage_events(tmp_path, caplog):
    """create/extract log progress events, not just a final one-line summary.

    Successful runs are required to "report progressive staging and
    publication" -- verify both commands
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
            return codec.Base16GCodec().encode(data, **options)

        def decode(self, lines, **options):
            return codec.Base16GCodec().decode(lines, **options)

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
        # The compact human header names the codec positionally (v1 <codec>...).
        assert "v1 plugin_codec" in archive_file.read_text(encoding="utf-8")
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


def test_compact_header_parses_without_sha_or_meta(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"

    cli.run(["create", "-f", str(archive_file), "-C", str(src), "."])
    header_line = archive_file.read_text(encoding="utf-8").splitlines()[0]
    from glyphive import layout

    parsed = layout.parse_header(header_line)
    # The compact display-only line carries v/codec/comp/files/bytes/pages but
    # NOT sha256 or meta (those live only in the protected H frames now).
    assert parsed["v"] == layout.LAYOUT_VERSION
    assert parsed["codec"] and parsed["comp"]
    assert "sha256" not in parsed and "meta" not in parsed


def test_no_header_omits_human_line_but_still_restores(tmp_path):
    """--no-header: page 1 has no '#!glyphive' line, yet restore is byte-exact."""
    src = _make_srcdir(tmp_path)
    with_header = tmp_path / "with.txt"
    without_header = tmp_path / "without.txt"

    assert cli.run(["create", "-f", str(with_header), "-C", str(src), "."]) == 0
    assert cli.run(
        ["create", "-f", str(without_header), "-C", str(src), "--no-header", "."]
    ) == 0

    assert with_header.read_text(encoding="utf-8").splitlines()[0].startswith("#!glyphive")
    without_lines = without_header.read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith("#!glyphive") for line in without_lines)

    outdir = tmp_path / "out"
    assert cli.run(["extract", "-f", str(without_header), "-C", str(outdir)]) == 0
    _compare_dirs(src, outdir)


def test_extra_comment_line_is_ignored_on_restore(tmp_path):
    """Any '#!' line is a display-only comment: injecting one does not break restore."""
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"
    assert cli.run(["create", "-f", str(archive_file), "-C", str(src), "."]) == 0

    lines = archive_file.read_text(encoding="utf-8").splitlines()
    lines.insert(1, "#! an arbitrary human note that restore must ignore")
    archive_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    outdir = tmp_path / "out"
    assert cli.run(["extract", "-f", str(archive_file), "-C", str(outdir)]) == 0
    _compare_dirs(src, outdir)


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
    # The 'basic' metadata profile is recorded in the protected H frames (not
    # the display-only human line, which no longer carries meta=).
    from glyphive import layout
    doc_lines = archive_file.read_text(encoding="utf-8").splitlines()
    restored_meta, _ = layout.read_pages(doc_lines)
    assert restored_meta.get("meta") == "basic"
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


@pytest.mark.parametrize(
    "codec", ["base32g-crc16-rs", "base64-crc16-rs", "base64g-crc16-rs"]
)
def test_create_line_width_reaches_denser_codecs(tmp_path, codec):
    """--line-width must reach every codec, not only base16g.

    Regression: the CLI previously passed line_width only to base16g and used
    ``options = {}`` for every other codec, so base32g/base64/base64g silently
    ignored --line-width and were locked to the 60-char default row.
    """
    src = _make_srcdir(tmp_path)
    (src / "wide.bin").write_bytes(bytes(range(256)) * 8)
    archive_file = tmp_path / "wide.txt"

    assert cli.run(
        [
            "create", "-f", str(archive_file), "-C", str(src),
            "--codec", codec, "--compression", "none",
            "--line-width", "100", ".",
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


@pytest.mark.parametrize("spelling", ["auto", "max"])
def test_create_line_width_auto_and_max_on_pdf(tmp_path, spelling):
    """`--line-width auto|max` are accepted on PDF; max fits >= auto's width."""
    pytest.importorskip("fpdf")
    src = _make_srcdir(tmp_path)
    out = tmp_path / f"{spelling}.pdf"
    rc = cli.run(
        [
            "create", "-f", str(out), "--format", "pdf",
            "--font", "ocr-b", "--font-size", "6",
            "--line-width", spelling, "-C", str(src), ".",
        ]
    )
    assert rc == 0
    assert out.exists()


def test_create_line_width_max_on_text_errors(tmp_path):
    src = _make_srcdir(tmp_path)
    with pytest.raises(SystemExit, match="max needs a format with physical font"):
        cli.run(
            ["create", "-f", str(tmp_path / "t.txt"), "--format", "text",
             "--line-width", "max", "-C", str(src), "."]
        )


def test_create_line_width_above_safe_cap_needs_force(tmp_path):
    pytest.importorskip("fpdf")
    src = _make_srcdir(tmp_path)

    def run(*extra):
        return cli.run(
            [
                "create", "-f", str(tmp_path / "w.pdf"), "--format", "pdf",
                "--font", "ocr-b", "--font-size", "6",
                *extra, "-C", str(src), ".",
            ]
        )

    # 80 > safe cap 60 for ocr-b 6pt -> rejected without --force.
    with pytest.raises(SystemExit, match="exceeds the OCR-measured-safe"):
        run("--line-width", "80")
    # With --force it is accepted (80 <= geometric fit ~90).
    assert run("--line-width", "80", "--force") == 0
    # Above the geometric fit it is rejected even with --force.
    with pytest.raises(SystemExit, match="exceeds even the geometric fit"):
        run("--line-width", "999", "--force")


def test_create_mode_defaults_to_standard(tmp_path):
    """Bare `create` (no --mode) resolves the same as --mode standard."""
    from glyphive.cli.create import Create

    bare = Create(file="x.txt", paths=["."])
    bare._apply_mode()
    explicit = Create(file="x.txt", paths=["."], mode="standard")
    explicit._apply_mode()
    assert (bare.codec, bare.font, bare.font_size, bare.line_width, bare.minimal_margins) == (
        explicit.codec, explicit.font, explicit.font_size, explicit.line_width, explicit.minimal_margins,
    )
    assert bare.codec == "base16g-crc16-rs"
    assert bare.font == "dejavu-sans-mono"
    assert bare.font_size == 6.0
    assert bare.line_width == "max"
    assert bare.minimal_margins is False


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("conservative", ("base16g-crc16-rs", "dejavu-sans-mono", 8.0, "auto", False)),
        ("standard", ("base16g-crc16-rs", "dejavu-sans-mono", 6.0, "max", False)),
        ("max", ("base16g-crc16-rs", "dejavu-sans-mono", 6.0, "max", True)),
    ],
)
def test_create_mode_presets_resolve_exact_fields(mode, expected):
    from glyphive.cli.create import Create

    c = Create(file="x.txt", paths=["."], mode=mode)
    c._apply_mode()
    assert (c.codec, c.font, c.font_size, c.line_width, c.minimal_margins) == expected


def test_create_mode_lets_explicit_flags_override_individual_fields(tmp_path):
    """Any of --codec/--font/--font-size/--line-width/--minimal-margins passed
    explicitly wins over the mode's preset for just that one field."""
    from glyphive.cli.create import Create

    c = Create(
        file="x.txt", paths=["."], mode="standard",
        font="courier", font_size=10.0, minimal_margins=True,
    )
    c._apply_mode()
    # Overridden fields keep the explicit value...
    assert c.font == "courier"
    assert c.font_size == 10.0
    assert c.minimal_margins is True
    # ...but codec/line_width (not overridden) still come from the mode.
    assert c.codec == "base16g-crc16-rs"
    assert c.line_width == "max"


def test_create_mode_line_width_max_degrades_to_auto_on_text(tmp_path):
    """--mode's line_width='max' must not hard-error on text output (no
    geometric metrics) the way an EXPLICIT --line-width max does -- it
    should quietly behave like 'auto' instead, since a mode is meant to be
    a sensible default across every format."""
    src = _make_srcdir(tmp_path)
    out = tmp_path / "t.txt"
    # No --mode passed -> defaults to 'standard' (line_width='max' from the
    # preset). Must NOT raise, unlike an explicit --line-width max on text.
    assert cli.run(["create", "-f", str(out), "--format", "text", "-C", str(src), "."]) == 0
    assert out.exists()


def test_create_mode_conservative_roundtrips_on_text(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"
    outdir = tmp_path / "out"
    assert cli.run(
        ["create", "-f", str(archive_file), "-C", str(src), "--mode", "conservative", "."]
    ) == 0
    assert cli.run(["extract", "-f", str(archive_file), "-C", str(outdir)]) == 0
    _compare_dirs(src, outdir)


def test_create_mode_max_roundtrips_on_text(tmp_path):
    src = _make_srcdir(tmp_path)
    archive_file = tmp_path / "a.txt"
    outdir = tmp_path / "out"
    assert cli.run(
        ["create", "-f", str(archive_file), "-C", str(src), "--mode", "max", "."]
    ) == 0
    assert cli.run(["extract", "-f", str(archive_file), "-C", str(outdir)]) == 0
    _compare_dirs(src, outdir)


def test_create_rejects_unknown_mode(tmp_path):
    src = _make_srcdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.run(
            ["create", "-f", str(tmp_path / "x.txt"), "-C", str(src),
             "--mode", "not-a-real-mode", "."]
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
            return codec.Base16GCodec().encode(data, **options)

        def decode(self, lines, **options):
            return codec.Base16GCodec().decode(lines, **options)

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
        # Compact header: codec and comp collapse to one positional token.
        assert "test_codec,test_compression" in header
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
        "load_input_lines_with_conf",
        lambda source, engine=None, blur=0.0, spine=None: seen.update(source=source, engine=engine) or ([], None),
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
            "--ocr-engine",
            "test-engine",
            "-C",
            str(tmp_path / "out"),
        ]
    ) == 0
    assert seen["engine"] == "test-engine"


def test_extract_threads_ocr_confidence_into_restore(tmp_path, monkeypatch):
    """extract must forward per-line OCR confidence from the image loader all the
    way to restore_document_spooled, so a scan's confidence actually reaches the
    codec's char-level erasure logic (not silently dropped at the CLI seam)."""
    from glyphive.cli import extract as extract_command
    from glyphive.restore import unarchive

    conf = [[0.9, 0.2], None]
    monkeypatch.setattr(
        extract_command,
        "load_input_lines_with_conf",
        lambda source, engine=None, blur=0.0, spine=None: (["L00000 AB #CD", "L00001 EF #GH"], conf),
    )
    captured = {}

    def fake_restore(lines, dest, **options):
        captured["char_conf"] = options.get("char_conf")
        return {"_page_warnings": []}, ["a.txt"]

    monkeypatch.setattr(unarchive, "restore_document_spooled", fake_restore)

    assert cli.run(
        ["extract", "-f", str(tmp_path / "scan.png"), "-C", str(tmp_path / "out")]
    ) == 0
    assert captured["char_conf"] == conf


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
    from glyphive.cli._common import load_input_lines
    from glyphive.restore import ocr

    images = tmp_path / "images"
    images.mkdir()
    (images / "b.png").write_bytes(b"b")
    (images / "a.png").write_bytes(b"a")
    (images / "nested").mkdir()
    seen = {"paths": [], "engines": []}

    def fake_ocr_pages(paths, *, engine=None):
        seen["paths"].append([path.name for path in paths])
        seen["engines"].append(engine)
        return [["page-a"]] if paths[0].name == "a.png" else [["page-b", "tail"]]

    monkeypatch.setattr(ocr, "ocr_pages", fake_ocr_pages)
    assert load_input_lines(images, engine="mock") == ["page-a", "page-b", "tail"]
    # Files are OCR'd one at a time, in sorted order -- not batched.
    assert seen == {
        "paths": [["a.png"], ["b.png"]],
        "engines": ["mock", "mock"],
    }


def test_empty_input_directory_fails_clearly(tmp_path):
    from glyphive.cli._common import load_transcript_lines

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="input directory contains no files"):
        load_transcript_lines(empty)


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


def test_inspect_reports_recovery_headroom_and_strict_exit(tmp_path, capsys):
    """`inspect` reports D/K/nsym read-only; --strict flags an unrecoverable doc."""
    from glyphive.render.formats.text import FORM_FEED

    src = _make_srcdir(tmp_path)
    (src / "d.txt").write_text("delta " * 400, encoding="utf-8")
    archive = tmp_path / "doc.txt"
    assert cli.run(
        # Pin a stable page geometry (width=auto; font-size 12, distinct from
        # both the class default 11 and mode presets' 6/8 so the explicit
        # override actually takes, and dense enough -- fewer lines/page than
        # 11pt -- to keep this test's >= 3 data-page requirement).
        ["create", "-f", str(archive), "-C", str(src),
         "--mode", "conservative", "--font-size", "12",
         "--compression", "none", "--parity-pages", "1", ".",]
    ) == 0

    # Intact: readable, exit 0, reports data/parity pages and the nsym budget.
    capsys.readouterr()
    assert cli.run(["inspect", "-f", str(archive)]) == 0
    out = capsys.readouterr().out
    assert "data +" in out and "parity" in out
    assert "Reed-Solomon budget" in out

    # No file is written by inspect.
    before = sorted(p.name for p in tmp_path.iterdir())
    cli.run(["inspect", "-f", str(archive)])
    assert sorted(p.name for p in tmp_path.iterdir()) == before

    # Drop 2 whole data pages (> K=1) -> unrecoverable; --strict exits non-zero,
    # plain inspect still exits 0.
    blocks = archive.read_text(encoding="utf-8").split(FORM_FEED)
    assert len(blocks) >= 4
    surviving = [b for i, b in enumerate(blocks) if i not in (1, 2)]
    archive.write_text(FORM_FEED.join(surviving), encoding="utf-8")

    assert cli.run(["inspect", "-f", str(archive)]) == 0
    assert cli.run(["inspect", "-f", str(archive), "--strict"]) != 0


def test_inspect_json_is_machine_readable(tmp_path, capsys):
    import json

    src = _make_srcdir(tmp_path)
    archive = tmp_path / "j.txt"
    assert cli.run(
        ["create", "-f", str(archive), "-C", str(src), "--compression", "none", "."]
    ) == 0
    capsys.readouterr()
    assert cli.run(["inspect", "-f", str(archive), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["readable"] is True
    assert payload["codec"] == "base16g-crc16-rs"
    assert "line_rs_nsym" in payload


def test_descan_auto_retries_with_blur_on_image_decode_failure(tmp_path, monkeypatch):
    """descan=auto retries a failed sharp image pass over the blur ladder."""
    from glyphive import layout
    from glyphive.cli import _common

    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")  # image magic so it's image input
    calls = []

    good_lines = ["decoded", "lines"]

    def fake_loader(source, *, engine=None, blur=None, spine=None):
        calls.append((list(blur) if blur is not None else None, spine))
        # First (sharp) call raises via the decode below; second returns good.
        # extract now consumes (lines, char_conf); char_conf is None here.
        return (["sharp"] if len(calls) == 1 else good_lines), None

    # Also patch the name imported into extract's module namespace.
    from glyphive.cli import extract as extract_mod
    monkeypatch.setattr(_common, "load_input_lines_with_conf", fake_loader)
    monkeypatch.setattr(extract_mod, "load_input_lines_with_conf", fake_loader)

    def fake_restore(lines, dest, **kw):
        if lines == ["sharp"]:
            raise layout.LayoutError("machine header frame copies failed")
        return {"_page_warnings": []}, ["a.txt"]

    from glyphive.restore import unarchive
    monkeypatch.setattr(unarchive, "restore_document_spooled", fake_restore)

    rc = cli.run(["extract", "-f", str(image), "-C", str(tmp_path / "o")])
    assert rc == 0
    # Two loads: the sharp [0.0] (no spine -- it's the first pass), then the
    # retry over the ADDITIONAL blur ladder only (0.6, 0.8 -- NOT 0.0, since
    # the sharp pass's own lines are threaded back in as the retry's spine so
    # it is never re-OCR'd; 0.8 matters because a real Courier-12 archive
    # decoded only at that radius).
    assert calls[0][0] == [0.0]
    assert calls[0][1] is None
    assert calls[1][0] == _common.AUTO_DESCAN_RETRY_RADII
    assert 0.0 not in calls[1][0]
    assert 0.8 in calls[1][0]
    # The retry's spine carries the sharp pass's own lines (as OcrLine).
    assert [line.text for line in calls[1][1]] == ["sharp"]


def test_descan_auto_does_not_retry_text_input(tmp_path, monkeypatch):
    """A text transcript failure is NOT retried with blur (blur can't help it)."""
    from glyphive import layout
    from glyphive.cli import extract as extract_mod

    transcript = tmp_path / "doc.txt"
    transcript.write_text("not a real transcript\n", encoding="utf-8")
    calls = []

    def fake_loader(source, *, engine=None, blur=None, spine=None):
        calls.append(list(blur) if blur is not None else None)
        return ["lines"], None  # extract now consumes (lines, char_conf)

    monkeypatch.setattr(extract_mod, "load_input_lines_with_conf", fake_loader)

    from glyphive.restore import unarchive
    monkeypatch.setattr(
        unarchive, "restore_document_spooled",
        lambda lines, dest, **kw: (_ for _ in ()).throw(
            layout.LayoutError("no glyphive header")
        ),
    )

    with pytest.raises(layout.LayoutError):
        cli.run(["extract", "-f", str(transcript), "-C", str(tmp_path / "o")])
    # Only the single sharp pass -- no blur retry for text input.
    assert calls == [[0.0]]


def test_descan_explicit_value_does_not_auto_retry(tmp_path):
    from glyphive.cli._common import resolve_descan

    assert resolve_descan("auto") == ([0.0], True)
    assert resolve_descan("0") == ([0.0], False)
    assert resolve_descan("0,0.6") == ([0.0, 0.6], False)


def test_footer_hash_advisory_logs_at_info_not_warning(caplog):
    """warn_page_integrity logs footer-hash advisories at INFO, warnings at WARNING."""
    import logging
    from glyphive.cli._common import warn_page_integrity

    logger = logging.getLogger("glyphive.test.footer")
    meta = {
        "_page_warnings": ["page 2/3: missing"],
        "_footer_hash_notes": ["page 1/3: footer hash X != Y"],
    }
    with caplog.at_level(logging.INFO, logger="glyphive.test.footer"):
        warn_page_integrity(logger, meta)

    records = {r.levelno: r.getMessage() for r in caplog.records}
    # The real warning is WARNING; the footer-hash advisory is INFO (not WARNING).
    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    info_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("missing" in m for m in warning_msgs)
    assert not any("footer hash" in m for m in warning_msgs)
    assert any("footer hash" in m for m in info_msgs)


@pytest.mark.parametrize("codec_name", [
    "base8g-crc16-rs", "base16-crc16-rs", "base16g-crc16-rs", "base32-crc16-rs",
    "base32c-crc16-rs", "base32g-crc16-rs", "base64-crc16-rs", "base64g-crc16-rs", "base85-crc16-rs",
    "basemaxg-crc16-rs", "z85-crc16-rs",
])
def test_denser_codecs_full_cli_roundtrip(tmp_path, codec_name):
    """create --codec <radix> then extract must restore byte-identical.

    Guards the layout classification path: L/P frames use the SELECTED codec's
    alphabet (base32g/base64 add symbols), which base16g's frame parser would
    reject -- read_pages must resolve the payload spec from the header. Unit
    codec tests call decode() directly and miss this; only the full CLI path
    (create -> read_pages -> decode) exercises it.
    """
    src = _make_srcdir(tmp_path)
    (src / "rand.bin").write_bytes(os.urandom(3000))
    archive = tmp_path / "a.txt"
    outdir = tmp_path / "out"
    assert cli.run(
        ["create", "-f", str(archive), "-C", str(src), "--codec", codec_name, "."]
    ) == 0
    assert cli.run(["extract", "-f", str(archive), "-C", str(outdir)]) == 0
    _compare_dirs(src, outdir)


def test_info_reports_registries_and_default_codec(capsys):
    assert cli.run(["info"]) == 0
    out = capsys.readouterr().out
    assert "codecs:" in out
    assert "base16g-crc16-rs (default)" in out
    assert "compression:" in out
    assert "render:" in out
    assert "ocr:" in out
    assert "fonts:" in out


def test_info_json_is_machine_readable(capsys):
    import json

    capsys.readouterr()
    assert cli.run(["info", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["codecs"]["default"] == "base16g-crc16-rs"
    assert any(
        entry["name"] == "base16g-crc16-rs" for entry in payload["codecs"]["entries"]
    )
    assert "compression" in payload
    assert "render_formats" in payload
    assert "ocr_engines" in payload
    assert "fonts" in payload
    assert payload["fonts"]["core"]
    assert payload["fonts"]["bundled"]


def test_info_font_lookup_resolves_core_bundled_and_missing(capsys, monkeypatch):
    from glyphive.render.formats import pdf as pdf_mod
    from pathlib_next import Path

    monkeypatch.setattr(pdf_mod, "_system_font_dirs", lambda: [])  # no system store

    capsys.readouterr()
    assert cli.run(["info", "--font", "courier"]) == 0
    assert "resolves (core)" in capsys.readouterr().out

    capsys.readouterr()
    assert cli.run(["info", "--font", "ocr-b"]) == 0
    assert "resolves (bundled)" in capsys.readouterr().out

    capsys.readouterr()
    assert cli.run(["info", "--font", "no-such-font-at-all"]) == 0
    assert "NOT found" in capsys.readouterr().out


def test_info_font_lookup_resolves_system_font_by_true_family_name(
    tmp_path, capsys, monkeypatch
):
    from fontTools.ttLib import TTFont
    from importlib import resources

    from glyphive.render.formats import pdf as pdf_mod
    from pathlib_next import Path

    fake_store = tmp_path / "fake-fonts"
    fake_store.mkdir()
    src = resources.files("glyphive.assets.fonts.ocr_b").joinpath("OCR-B.ttf")
    renamed = fake_store / "totally-unrelated-filename.ttf"
    with resources.as_file(src) as font_path:
        font = TTFont(str(font_path))
    for record in font["name"].names:
        if record.nameID in (1, 4, 6, 16):
            record.string = "Test Family Name"
    font.save(str(renamed))
    monkeypatch.setattr(pdf_mod, "_system_font_dirs", lambda: [Path(str(fake_store))])

    capsys.readouterr()
    assert cli.run(["info", "--font", "Test Family Name"]) == 0
    out = capsys.readouterr().out
    assert "resolves (system:" in out
    assert "totally-unrelated-filename.ttf" in out
