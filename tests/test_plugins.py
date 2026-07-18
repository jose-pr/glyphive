"""Tests for explicit installed-entry-point discovery."""

from __future__ import annotations

import pytest

from glyphive import codec, compression, render
from glyphive import plugins
from glyphive.restore import ocr

#: Built-in codecs (base16c default + the denser radix family), sorted.
_BUILTIN_CODECS = [
    "base16-crc16-rs", "base16g-crc16-rs", "base32-crc16-rs", "base32c-crc16-rs",
    "base32g-crc16-rs", "base64-crc16-rs", "base8-crc16-rs", "base85-crc16-rs",
    "basemaxg-crc16-rs", "z85-crc16-rs",
]


class FakeEntryPoints(list):
    def select(self, *, group):
        return FakeEntryPoints(item for item in self if item.group == group)


class FakeEntryPoint:
    def __init__(self, group, name, obj=None, *, error=None, distribution="test-dist"):
        self.group = group
        self.name = name
        self._obj = obj
        self._error = error
        self.dist = type("Dist", (), {"name": distribution})()
        self.loads = 0

    def load(self):
        self.loads += 1
        if self._error is not None:
            raise self._error
        return self._obj


@pytest.fixture(autouse=True)
def reset_plugins():
    plugins._reset_for_tests()
    yield
    plugins._reset_for_tests()


def _external_classes():
    class TestCodec(codec.Codec):
        name = "external_codec"

        def encode(self, data, **options):
            return codec.Base16GCodec().encode(data, **options)

        def decode(self, lines, **options):
            return codec.Base16GCodec().decode(lines, **options)

    class TestCompression(compression.CompressionMethod):
        name = "external_compression"

        def compress(self, data, level=None):
            return data

        def decompress(self, data):
            return data

    class TestRenderer(render.RenderFormat):
        name = "external_format"

        def render(self, pages, out, **options):
            return None

    class TestOcr(ocr.OcrProvider):
        name = "external_ocr"

        def ocr_image(self, image_path):
            return []

    classes = (TestCodec, TestCompression, TestRenderer, TestOcr)
    for implementation, base in zip(
        classes,
        (codec.Codec, compression.CompressionMethod, render.RenderFormat, ocr.OcrProvider),
    ):
        base._discard_implementation(implementation)
    return classes


def test_all_groups_load_deterministically_and_cache(monkeypatch):
    implementations = _external_classes()
    entries = FakeEntryPoints(
        [
            FakeEntryPoint("glyphive.render_formats", "external_format", implementations[2]),
            FakeEntryPoint("glyphive.codecs", "external_codec", implementations[0]),
            FakeEntryPoint("glyphive.ocr_providers", "external_ocr", implementations[3]),
            FakeEntryPoint(
                "glyphive.compression", "external_compression", implementations[1]
            ),
        ]
    )
    monkeypatch.setattr(plugins.metadata, "entry_points", lambda: entries)

    report = plugins.discover()
    assert [(item.group, item.name) for item in report.loaded] == [
        ("glyphive.codecs", "external_codec"),
        ("glyphive.compression", "external_compression"),
        ("glyphive.ocr_providers", "external_ocr"),
        ("glyphive.render_formats", "external_format"),
    ]
    assert report.errors == ()
    assert plugins.discover() is report
    assert all(entry.loads == 1 for entry in entries)
    assert codec.get("external_codec").name == "external_codec"
    assert compression.get("external_compression").name == "external_compression"
    assert render.get("external_format").name == "external_format"
    assert ocr.get("external_ocr").name == "external_ocr"


def test_invalid_broken_and_colliding_candidates_are_isolated(monkeypatch):
    class WrongName(codec.Codec):
        name = "different"

        def encode(self, data, **options):
            return []

        def decode(self, lines, **options):
            return b""

    codec.Codec._discard_implementation(WrongName)
    entries = FakeEntryPoints(
        [
            FakeEntryPoint("glyphive.codecs", "wrong", WrongName),
            FakeEntryPoint("glyphive.codecs", "base16g-crc16-rs", codec.Base16GCodec),
            FakeEntryPoint("glyphive.codecs", "broken", error=RuntimeError("boom")),
            FakeEntryPoint("glyphive.compression", "not-a-class", object()),
        ]
    )
    monkeypatch.setattr(plugins.metadata, "entry_points", lambda: entries)

    report = plugins.discover()
    assert report.loaded == ()
    assert len(report.errors) == 4
    messages = "\n".join(error.message for error in report.errors)
    assert "boom" in messages
    assert "does not match" in messages
    assert "duplicate codec" in messages
    assert "not a CompressionMethod subclass" in messages
    assert codec.names() == _BUILTIN_CODECS


def test_reset_removes_only_external_classes(monkeypatch):
    test_codec = _external_classes()[0]
    monkeypatch.setattr(
        plugins.metadata,
        "entry_points",
        lambda: FakeEntryPoints(
            [FakeEntryPoint("glyphive.codecs", "external_codec", test_codec)]
        ),
    )
    builtins = tuple(codec.names())
    plugins.discover()
    assert "external_codec" in codec.names()
    plugins._reset_for_tests()
    assert tuple(codec.names()) == builtins


def test_no_discovery_without_explicit_call(monkeypatch):
    monkeypatch.setattr(
        plugins.metadata,
        "entry_points",
        lambda: pytest.fail("entry points were enumerated eagerly"),
    )
    assert codec.names() == _BUILTIN_CODECS
    assert "gzip" in compression.names()
