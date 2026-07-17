"""Convert PDF or DOCX pages to PNG images for OCR troubleshooting."""

from __future__ import annotations

import argparse

from glyphive.restore.document_images import render_document_images


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="PDF or DOCX input")
    parser.add_argument("destination", help="directory for ordered PNG pages")
    parser.add_argument(
        "--dpi", type=int, default=300, help="render DPI (default: 300)"
    )
    parser.add_argument(
        "--blur", type=float, default=0.0, help="Gaussian blur radius in pixels"
    )
    args = parser.parse_args()
    for output in render_document_images(
        args.source, args.destination, dpi=args.dpi, blur=args.blur
    ):
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
