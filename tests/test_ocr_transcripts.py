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

# Captured verbatim from a real Tesseract 5.4.0 OCR pass over a rasterized
# (300 DPI, Courier 8pt) glyphive PDF page, against the current wire format
# (RS-protected machine header, base16c-crc16-rs codec). Do not "clean up"
# the OCR noise below -- the inserted interior spaces and the corrupted
# prose header (``codec=basel6c-crcl6-rs``, a garbled sha256) are the point
# of the fixture: restore must recover via CRC/RS, never by guessing.
_CAPTURED_NONE_TRANSCRIPT = """\
#!glyphive v=1 codec=basel6c-crcl6-rs comp=none meta=none files=1 bytes=84 pages=1 sha256=8c205c35542af4141b550d7427 6ebX71d844b045dc35afclc29e50e9a3bfab92
HMYCVH HMHPDBAAKAABBALCLBMDLKDBDLLDCYLDMCLDDBDLCYMCMDAHL3L4L3LKAHL3 #RCLB
HMYCVH HMHPDBAAKAABBALCLBMDLKDBDLLDCYLDMCLDDBDLCYMCMDAHL3L4L3LKAHL3 #RCLB
HMYCVK L4L3LKAAAAAAAAAAAAAAABAAAAAAAAAAAAAAKHAAAAAAABPXCAKXDKKHCT4H #YYAP
HMYCVK L4L3LKAAAAAAAAAAAAAAABAAAAAAAAAAAAAAKHAAAAAAABPXCAKXDKKHCT4H #YYAP
HMYCVL BHBVKKAYMHCML3V4MBY PHHVAHKYXDKT4XBXCR3KA3RTDV4TVRCTRYPPY4TXY #PPT3
HMYCVL BHBVKKAYMHCML3V4MBY PHHVAHKYXDKT4XBXCR3KA3RTDV4TVRCTRYPPY4TXY #PPT3
HMYCVM CXRHMX #HRYB
HMYCVM CXRHMX #HRYB
HMYCVA MHTMKBCYAKHMTLYRK4LD3BTRCDVBHL3LCAVP3DCBHYXTTC4MYL3XYVXHDPTK #YRPR
HMYCVA MHTMKBCYAKHMTLYRK4LD3BTRCDVBHL3LCAVP3DCBHYXTTC4MYL3XYVXHDPTK #YRPR
LMYCVH HCDBABAVAAAAAAKHHMHXKRKAH PHRKLDBACAAABAAAAAAAAAKAALBC 3MHMPMHDLAAAAAA #MLHD
LMYCVK AAAAAAAALPLKLXLXL4CALMLXMRMALPLRMLLKCALMLBMHLKCAMHLKMDMHCAKHLPMKCAHT #3HPX
LMYCVL MKLXCADBDLCADCDCDTDKDHDTDHDDCAHKHHKHCADCDADCDLAT #K3TX
PMYCVH PTVVHBPVK4HYXR4RBMXLKM #THPY
TMYCVH HMKHDBAAAAAAABADVAALLA4PRT3R4M #XY34 PAGE 1/1
"""


def test_captured_ocr_noise_still_parses_and_restores():
    """A real captured OCR transcript restores despite header/space noise.

    The prose header's ``codec=`` value and ``sha256`` are corrupted by OCR
    (``basel6c-crcl6-rs``, a mid-hex space and a substituted char) -- restore
    must not trust or repair them by guessing; the H-frame CRC/RS oracle is
    authoritative. The payload/parity lines have OCR-inserted interior
    spaces, which the structural frame parser (label-first, ``#check``-last)
    must tolerate without discarding the line.
    """
    lines = _CAPTURED_NONE_TRANSCRIPT.strip("\n").splitlines()
    meta, encoded = layout.read_pages(lines)

    assert meta["codec"] == "base16c-crc16-rs"
    assert meta["comp"] == "none"
    assert meta["bytes"] == 84
    restored = codec.get(meta["codec"]).decode(encoded)
    assert len(restored) == meta["bytes"]
    assert restored.startswith(b"GLYPHIV1")


def test_captured_transcript_tolerates_extra_ocr_junk_lines():
    """A stray unparseable OCR line (blank/garbage) does not break restore.

    OCR frequently emits blank lines or unrelated noise between real frame
    lines (visible in the fixture itself). Insert an extra garbage line and
    confirm it is silently ignored rather than corrupting the result.
    """
    lines = _CAPTURED_NONE_TRANSCRIPT.strip("\n").splitlines()
    noisy = lines[:3] + ["xJ(garbled OCR artifact)9~"] + lines[3:]

    meta, encoded = layout.read_pages(noisy)

    assert meta["codec"] == "base16c-crc16-rs"
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
    (src / "a.txt").write_text(
        "hello glyphive gate test across engines and compression\n",
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

    doc = pypdfium2.PdfDocument(str(pdf_path))
    try:
        assert len(doc) == 1
        image = doc[0].render(scale=300 / 72).to_pil().convert("L")
    finally:
        doc.close()
    page_path = tmp_path / "gate-page001.png"
    image.save(str(page_path))

    rc = cli.run(
        [
            "extract",
            "-f",
            str(page_path),
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
