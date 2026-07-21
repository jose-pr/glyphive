"""Generate the physical-scan regression fixture.

Produces a tiny, non-sensitive synthetic tree, archives it with glyphive
(``create --format pdf --compression none``), rasterizes the resulting PDF at
300 DPI (the same DPI ``tests/test_ocr_transcripts.py`` uses), and applies a
deterministic, realistic degradation with Pillow (Gaussian blur + a small
rotation, fixed parameters, no randomness) to stand in for a phone-camera scan
without committing any real user content.

Regenerate the fixture with:

    .venv/3.14-nt-amd64/Scripts/python tests/fixtures/physical_scan/generate.py

This overwrites ``pages/*.png``, ``MANIFEST.txt``, and ``payload.bin`` in this
directory (not this script itself). Page images live in the ``pages/``
subdirectory (not this directory directly) so ``extract --from-images`` can
point at a directory containing only images -- ``_input_files`` treats every
direct-child file of an image directory as a candidate page. After
regenerating, re-run the Phase 2 gate test in
``tests/test_ocr_transcripts.py`` (with Tesseract on PATH) to confirm the
fixture still restores byte-for-byte before committing.

Degradation parameters (deterministic, no randomness):
    BLUR_RADIUS = 0.6   (Gaussian blur radius, Pillow ImageFilter.GaussianBlur)
    ROTATION_DEGREES = 0.7  (small skew, Pillow Image.rotate, expand=True,
                              fillcolor=255 so the new corners are page-white)

These were chosen to be representative of real phone-scan artifacts (see
``.agents/plans/physical_scan_regression_gate.md``) while still restoring
reliably through ``extract --from-images --ocr-engine tesseract-glyphive``
with the auto ``--descan`` retry ladder (``[0.0, 0.6, 0.8]``). If a future
change to this script increases the degradation, re-verify the fixture still
restores -- the gate must PASS on healthy code, so degradation must never be
tuned past what the auto-descan retry can recover.
"""

from __future__ import annotations

from pathlib_next import Path

BLUR_RADIUS = 0.6
ROTATION_DEGREES = 0.7
RASTER_DPI = 300

FIXTURE_DIR = Path(__file__).parent
PAGES_DIR = FIXTURE_DIR / "pages"
PAYLOAD_NAME = "payload.bin"
MANIFEST_NAME = "MANIFEST.txt"


def _build_source_tree(src: Path) -> None:
    """A tiny, non-sensitive synthetic tree: a couple of short text files."""
    (src / "notes").mkdir(parents=True, exist_ok=True)
    (src / "notes" / "hello.txt").write_text(
        "hello glyphive physical-scan regression fixture\n"
        "the quick brown fox jumps over the lazy dog\n" * 3,
        encoding="utf-8",
    )
    (src / "readme.txt").write_text(
        "This is a small synthetic tree used only to regression-test the\n"
        "physical-scan restore gate. It carries no real user content.\n",
        encoding="utf-8",
    )


def _archive_payload_bytes(src: Path, tmp_dir: Path) -> bytes:
    """Run the real archive_tree path so the payload matches what `create` embeds."""
    from glyphive import archive as _archive

    return _archive.archive_tree(src)


def _degrade(image):
    """Apply a deterministic Gaussian blur + small rotation (no randomness)."""
    from PIL import ImageFilter

    blurred = image.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
    rotated = blurred.rotate(
        ROTATION_DEGREES, expand=True, fillcolor=255, resample=0
    )
    return rotated


def generate() -> None:
    import shutil
    import tempfile

    from glyphive import cli as _cli

    with tempfile.TemporaryDirectory(prefix="glyphive-fixture-") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "src"
        src.mkdir()
        _build_source_tree(src)

        payload = _archive_payload_bytes(src, tmp_path)

        pdf_path = tmp_path / "fixture.pdf"
        rc = _cli.run(
            [
                "create",
                "-f",
                str(pdf_path),
                "--format",
                "pdf",
                "--none",
                "--metadata",
                "none",
                "-C",
                str(src),
                ".",
            ]
        )
        assert rc == 0, "glyphive create failed while generating the fixture"

        import pypdfium2

        PAGES_DIR.mkdir(parents=True, exist_ok=True)
        for existing in PAGES_DIR.glob("*.png"):
            existing.unlink()

        doc = pypdfium2.PdfDocument(str(pdf_path))
        try:
            page_count = len(doc)
            assert 1 <= page_count <= 3, (
                f"expected a 1-3 page fixture, got {page_count} pages -- "
                "shrink the synthetic tree"
            )
            for index in range(page_count):
                image = doc[index].render(scale=RASTER_DPI / 72).to_pil().convert("L")
                degraded = _degrade(image)
                out_path = PAGES_DIR / f"scan-page{index + 1:03d}.png"
                degraded.save(str(out_path), format="PNG")
        finally:
            doc.close()

    (FIXTURE_DIR / PAYLOAD_NAME).write_bytes(payload)
    (FIXTURE_DIR / MANIFEST_NAME).write_text(
        "Physical-scan regression fixture. Generated by generate.py -- do not "
        "hand-edit. Non-sensitive synthetic content only.\n"
        f"pages={page_count}\n"
        f"raster_dpi={RASTER_DPI}\n"
        f"blur_radius={BLUR_RADIUS}\n"
        f"rotation_degrees={ROTATION_DEGREES}\n"
        f"payload_bytes={len(payload)}\n",
        encoding="utf-8",
    )
    print(
        f"Generated {page_count} page(s) in {PAGES_DIR} + "
        f"{MANIFEST_NAME} + {PAYLOAD_NAME} in {FIXTURE_DIR}"
    )


if __name__ == "__main__":
    generate()
