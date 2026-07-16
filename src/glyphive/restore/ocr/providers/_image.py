"""Lazy image loading shared by OCR providers."""

from pathlib_next import Path


def load_image(image_path):
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "Pillow is required to read image files; install it with "
            "pip install glyphive[ocr]"
        ) from exc
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"image not found: {path}")
    import io

    return Image.open(io.BytesIO(path.read_bytes())).convert("RGB")
