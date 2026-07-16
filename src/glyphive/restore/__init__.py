"""Restore-side subpackage: printed/scanned glyphive pages back to bytes.

Contains the OCR orchestration layer (:mod:`glyphive.restore.ocr`) and, owned
by other phases, the text-transcript decode + unarchive path. Importing this
package pulls in no heavy optional dependencies.
"""

from .decode import RestoreError, decode_document
from .unarchive import restore_document, unarchive_bytes

__all__ = [
    "RestoreError",
    "decode_document",
    "unarchive_bytes",
    "restore_document",
]
