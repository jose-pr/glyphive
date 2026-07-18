"""Real-OCR regression coverage for the printable transcript grammar.

Two layers, per Phase 6 of ``.agents/plans/codec_naming_and_ocr_safe_index.md``:

1. A captured-transcript regression test (plain string fixture, no image or
   OCR engine dependency) so the frame-parsing/CRC/RS grammar itself is
   always exercised in CI, independent of which OCR engines happen to be
   installed on a given runner.
2. A real end-to-end gate (``create`` -> rasterize -> OCR -> ``extract`` ->
   byte comparison) parametrized over every registered OCR provider name
   (``glyphive.restore.ocr.names()``, currently 4) x 2 compression modes
   (``none``, ``zstd``) = 8 cases, skipping cleanly when an engine's
   binary/model is not installed. Stated expected skip count: with no OCR
   engine installed, all 8 skip (verified); with only Tesseract installed
   (this project's Windows/CI baseline: ``tesseract`` +
   ``tesseract-glyphive``), 4 run and 4 skip (verified).
"""

from __future__ import annotations

import pytest

from glyphive import cli, codec, layout
from glyphive.restore import ocr as _ocr


# --------------------------------------------------------------------------- #
# Layer 1: captured-transcript regression (no image/engine dependency)
# --------------------------------------------------------------------------- #

# A transcript is generated at test time (via ``create``) rather than pasted as
# a frozen string, then the real OCR-noise transforms are applied on top: a
# GARBLED display-only prose header (corrupted ``codec=``/``sha256`` — restore
# must ignore it and trust the CRC/RS H frames), and an OCR-inserted interior
# space in a payload line (the structural label-first/#check-last parser must
# tolerate it). Generating rather than freezing keeps the fixture correct across
# wire changes (e.g. the base16c -> base16g rename) instead of rotting.
def _noisy_none_transcript(tmp_path):
    """Return (clean_lines, noisy_lines, expected_bytes) for a 1-file archive."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "f.bin").write_bytes(bytes(range(84)))
    archive = tmp_path / "a.txt"
    assert cli.run(
        ["create", "-f", str(archive), "-C", str(src), "--none", "--metadata", "none", "."]
    ) == 0
    clean = archive.read_text(encoding="utf-8").strip("\n").splitlines()

    noisy = []
    injected = False
    for line in clean:
        if line.startswith("#!"):
            # Garble the display-only prose header (codec + sha256). Restore
            # must NOT trust or repair it.
            noisy.append(
                "#!glyphive v=1 codec=basel6g-crcl6-rs comp=none meta=none "
                "files=1 bytes=84 pages=1 sha256=743565e37d9ee0garbled 2"
            )
            continue
        if line.startswith("L") and not injected:
            # Inject one OCR interior space into the payload.
            parts = line.split()
            if len(parts) >= 3:
                payload = parts[1]
                mid = len(payload) // 2
                line = f"{parts[0]} {payload[:mid]} {payload[mid:]} {parts[2]}"
                injected = True
        noisy.append(line)
    assert injected, "expected at least one L line to inject noise into"
    return clean, noisy


def test_captured_ocr_noise_still_parses_and_restores(tmp_path):
    """A transcript restores despite a garbled prose header and interior spaces.

    The prose ``#!glyphive`` header's ``codec=`` and ``sha256`` are corrupted --
    restore must not trust or repair them by guessing; the H-frame CRC/RS oracle
    is authoritative. A payload line has an OCR-inserted interior space, which
    the structural frame parser (label-first, ``#check``-last) must tolerate.
    """
    _clean, noisy = _noisy_none_transcript(tmp_path)
    meta, encoded = layout.read_pages(noisy)

    assert meta["codec"] == "base16g-crc16-rs"
    assert meta["comp"] == "none"
    restored = codec.get(meta["codec"]).decode(encoded)
    assert len(restored) == meta["bytes"]
    assert restored.startswith(b"GLYPHIV1")


def test_captured_transcript_tolerates_extra_ocr_junk_lines(tmp_path):
    """A stray unparseable OCR line (blank/garbage) does not break restore."""
    _clean, noisy = _noisy_none_transcript(tmp_path)
    noisy = noisy[:3] + ["xJ(garbled OCR artifact)9~"] + noisy[3:]

    meta, encoded = layout.read_pages(noisy)

    assert meta["codec"] == "base16g-crc16-rs"
    restored = codec.get(meta["codec"]).decode(encoded)
    assert len(restored) == meta["bytes"]


# --------------------------------------------------------------------------- #
# Layer 2: real end-to-end gate, parametrized over installed OCR engines
# --------------------------------------------------------------------------- #

_ALL_ENGINE_NAMES = _ocr.names()


def _engine_available(name: str) -> bool:
    try:
        return _ocr.get(name).is_available()
    except Exception:
        return False


@pytest.mark.parametrize("compression", ["none", "zstd"])
@pytest.mark.parametrize("engine", _ALL_ENGINE_NAMES)
def test_real_ocr_gate_restores_byte_for_byte(tmp_path, engine, compression):
    """create -> rasterize 300 DPI -> real OCR -> extract -> diff.

    Skips cleanly (not a failure) when ``engine`` is not installed. Expected
    skip count: ``len(_ALL_ENGINE_NAMES) * 2`` engine/compression cases minus
    however many engines are actually installed on this runner times 2; with
    no OCR engine installed, all cases skip. With only Tesseract installed
    (this project's CI baseline), the 2 tesseract/tesseract-glyphive cases
    for ``none`` and ``zstd`` each run (up to 4) and the rest skip.
    """
    if not _engine_available(engine):
        pytest.skip(f"OCR engine {engine!r} is not installed on this runner")
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        pytest.skip("pypdfium2 is not installed (glyphive[document-input])")

    src = tmp_path / "src"
    src.mkdir()
    # A handful of data/parity lines gives Reed-Solomon too little budget to
    # correct even one genuine OCR misread (measured on the VM's Tesseract
    # 4.1.1: a single wrong character in a 3-data-line stream exceeded the RS
    # budget deterministically). Use a large-enough payload that the default
    # 12% parity_ratio yields a real multi-line correction budget, matching
    # how the codec is actually meant to be used.
    (src / "a.txt").write_text(
        "hello glyphive gate test across engines and compression\n" * 200,
        encoding="utf-8",
    )
    pdf_path = tmp_path / "gate.pdf"
    out_dir = tmp_path / "out"

    rc = cli.run(
        [
            "create",
            "-f",
            str(pdf_path),
            "--format",
            "pdf",
            "--compression",
            compression,
            "-C",
            str(src),
            ".",
        ]
    )
    assert rc == 0

    import pypdfium2

    image_dir = tmp_path / "pages"
    image_dir.mkdir()
    doc = pypdfium2.PdfDocument(str(pdf_path))
    try:
        assert len(doc) >= 1
        for index in range(len(doc)):
            image = doc[index].render(scale=300 / 72).to_pil().convert("L")
            image.save(str(image_dir / f"gate-page{index + 1:03d}.png"))
    finally:
        doc.close()

    rc = cli.run(
        [
            "extract",
            "-f",
            str(image_dir),
            "--from-images",
            "--ocr-engine",
            engine,
            "-C",
            str(out_dir),
        ]
    )
    assert rc == 0

    restored_text = (out_dir / "a.txt").read_text(encoding="utf-8")
    assert restored_text == (src / "a.txt").read_text(encoding="utf-8")
