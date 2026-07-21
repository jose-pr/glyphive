"""OCR model training for glyphive codecs (``glyphive train``).

Building a model that actually helps is mostly a *data integrity* problem, not
a hyperparameter problem. Every model this project shipped before this module
existed was trained wrong, and each failure was a process defect that this
module turns into a hard, loud abort:

- **Framed ground truth only.** Training on unframed alphabet strings
  (``"".join(random_chars)``) produces a model that hallucinates at frame
  boundaries and scores ~0% CER while failing real restore. Ground truth here
  is always what ``create`` actually printed.
- **Verified image/text pairing.** Row crops must be paired with the text that
  was printed on *that* row. Two independent off-by-one bugs (a display-only
  banner row, and a geometric row-count estimate that over-counted a page's
  real rows) silently taught a model to emit characters that were not there.
  :func:`verify_pairs` samples the built data and refuses to train on it if
  the pairs disagree.
- **Narrowed unicharset.** Fine-tuning from ``eng`` while passing ``eng``'s own
  unicharset leaves the network free to emit any English character. The
  unicharset here is derived from the codec's own alphabet, and
  ``--old_traineddata`` is passed because the unicharset then differs from the
  base model's.
- **No silent skips.** ``lstmtraining`` reports unencodable transcriptions and
  a skip ratio and then trains happily on what is left; a 35% skip once went
  unnoticed. Any encode failure aborts.
- **Byte-restore is the acceptance gate, never CER.** Three separate models
  scored ~0% CER and failed to restore a single document. CER is reported as a
  labelled proxy only.

Nothing here writes outside the caller's chosen output directory, and no path
is hardcoded: the VM-era scripts this replaces baked in ``/root/...``.
"""

from __future__ import annotations

from .data import (
    GroundTruthRow,
    PairCheckResult,
    build_training_rows,
    verify_pairs,
)
from .model import (
    ToolchainError,
    TrainingError,
    TrainingPlan,
    build_unicharset,
    check_toolchain,
    plan_training,
)

__all__ = [
    "GroundTruthRow",
    "PairCheckResult",
    "TrainingError",
    "TrainingPlan",
    "ToolchainError",
    "build_training_rows",
    "build_unicharset",
    "check_toolchain",
    "plan_training",
    "verify_pairs",
]
