#!/usr/bin/env bash
# Build tesseract's training tools from source (Rocky 9 has no packaged
# leptonica-devel / tesseract training tools) and fine-tune per-font,
# per-alphabet LSTM models for glyphive's printable channels.
#
# Alphabets trained (one model each, per font):
#   base16c  - ABCDHKLMPRTVXY34 (the shipped codec alphabet)
#   base64   - RFC 4648 base64 (A-Za-z0-9+/), for a future higher-radix codec
#   ascii    - full printable ASCII, general-purpose baseline
#
# Fonts trained: Courier (core PDF font, system-available) and OCR-B
# (glyphive's bundled OCR-B.ttf).
#
# Runs unattended; logs progress to training.log in this directory. Intended
# to be launched once and left running (LSTM fine-tuning is real wall-clock
# work, not something to babysit interactively).
set -euo pipefail

ROOT="/root/glyphive-ocr-training"
LOG="$ROOT/training.log"
mkdir -p "$ROOT"
exec > >(tee -a "$LOG") 2>&1

echo "=== $(date -Is) starting build_and_train.sh ==="

# --- 1. Build leptonica from source (no -devel package available) ----------
if [ ! -f /usr/local/lib/liblept.so ] && [ ! -f /usr/local/lib64/liblept.so ]; then
  echo "--- building leptonica ---"
  cd "$ROOT"
  if [ ! -d leptonica ]; then
    git clone --depth 1 --branch 1.85.0 https://github.com/DanBloomberg/leptonica.git
  fi
  cd leptonica
  ./autobuild 2>/dev/null || autoreconf -fi
  ./configure --prefix=/usr/local
  make -j"$(nproc)"
  make install
  ldconfig
fi

# --- 2. Build tesseract (with training tools) from source ------------------
# Rebuild whenever the training tools themselves are missing (not just
# `tesseract` on PATH) -- an earlier partial run can leave the base binary
# built but ENABLE_TRAINING configured false because pango-devel/cairo-devel
# were not yet installed (both are hard requirements for training tools,
# checked at ./configure time, not just at link time).
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:/usr/local/lib64/pkgconfig
export LD_LIBRARY_PATH=/usr/local/lib:/usr/local/lib64
if ! command -v text2image >/dev/null 2>&1 || ! command -v lstmtraining >/dev/null 2>&1; then
  echo "--- building tesseract with training tools ---"
  cd "$ROOT"
  if [ ! -d tesseract ]; then
    git clone --depth 1 --branch 5.4.1 https://github.com/tesseract-ocr/tesseract.git
  fi
  cd tesseract
  if ! pkg-config --exists icu-uc icu-i18n pango cairo pangocairo pangoft2; then
    echo "FATAL: training tool dependencies (icu/pango/cairo dev packages) are" >&2
    echo "still missing; install them before re-running this script." >&2
    exit 1
  fi
  [ -f ./configure ] || ./autogen.sh
  ./configure --prefix=/usr/local
  make -j"$(nproc)"
  make install
  make training -j"$(nproc)"
  make training-install
  ldconfig
fi

export PATH="/usr/local/bin:$PATH"
export LD_LIBRARY_PATH="/usr/local/lib:/usr/local/lib64:${LD_LIBRARY_PATH:-}"

echo "--- tool check ---"
text2image --version 2>&1 | head -3 || { echo "text2image build failed"; exit 1; }
lstmtraining --version 2>&1 | head -3 || { echo "lstmtraining build failed"; exit 1; }

# --- 3. tesseract_best/eng.traineddata as the fine-tuning base --------------
TESSDATA_BEST="$ROOT/tessdata_best"
mkdir -p "$TESSDATA_BEST"
if [ ! -f "$TESSDATA_BEST/eng.traineddata" ]; then
  curl -sL -o "$TESSDATA_BEST/eng.traineddata" \
    https://github.com/tesseract-ocr/tessdata_best/raw/main/eng.traineddata
fi

# --- 4. Alphabets and fonts -------------------------------------------------
declare -A ALPHABETS=(
  [base16c]="ABCDHKLMPRTVXY34"
  [base64]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
  [ascii]=" !\"#\$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_\`abcdefghijklmnopqrstuvwxyz{|}~"
)
FONTS_DIR="$ROOT/fonts"
mkdir -p "$FONTS_DIR"
# OCR-B bundled with glyphive; Courier is a system/core font text2image can
# reach by family name once fontconfig knows about it (core PDF fonts have no
# real screen-rendering TTF, so Liberation Mono substitutes for a genuine
# "generic monospace" comparison point instead).
cp /root/glyphive_ci/src/glyphive/assets/fonts/ocr_b/OCR-B.ttf "$FONTS_DIR/" 2>/dev/null || true
dnf install -y liberation-mono-fonts 2>&1 | tail -3 || true
FONT_LIST=("OCR-B" "Liberation Mono")

WORK="$ROOT/work"
mkdir -p "$WORK"

train_one() {
  local alphabet_name="$1" chars="$2" font="$3"
  local tag="${alphabet_name}_$(echo "$font" | tr ' ' '_')"
  local dir="$WORK/$tag"
  mkdir -p "$dir"
  cd "$dir"

  echo "=== $(date -Is) training $tag ==="

  # 4a. Repeat the alphabet into a training corpus large enough for text2image
  # to produce a real multi-line sample set (a few thousand characters).
  local n_repeats=400
  python3 - "$chars" "$n_repeats" > training_text.txt <<'PYEOF'
import random
import sys

chars = sys.argv[1]
n = int(sys.argv[2])
rng = random.Random(hash(chars) & 0xFFFFFFFF)
lines = []
for _ in range(n):
    length = rng.randint(40, 70)
    lines.append("".join(rng.choice(chars) for _ in range(length)))
print("\n".join(lines))
PYEOF

  text2image \
    --text=training_text.txt \
    --outputbase="${tag}.exp0" \
    --font="$font" \
    --fonts_dir="$FONTS_DIR" \
    --ptsize=8 \
    --resolution=300 \
    --unicharset_size_reserve=200

  unicharset_extractor "${tag}.exp0.box"
  mv unicharset "${tag}.unicharset" 2>/dev/null || true

  # 4b. Fine-tune from tessdata_best/eng.traineddata (LSTM fine-tune, not
  # from-scratch training -- far less data/time needed, appropriate for a
  # narrow printable-alphabet channel).
  combine_tessdata -e "$TESSDATA_BEST/eng.traineddata" eng.lstm

  lstmtraining \
    --continue_from eng.lstm \
    --model_output "${tag}_model" \
    --traineddata "$TESSDATA_BEST/eng.traineddata" \
    --train_listfile <(echo "$dir/${tag}.exp0.lstmf") \
    --max_iterations 800 \
    2>&1 | tail -40

  lstmtraining \
    --stop_training \
    --continue_from "${tag}_model_checkpoint" \
    --traineddata "$TESSDATA_BEST/eng.traineddata" \
    --model_output "${tag}.traineddata"

  echo "=== $(date -Is) finished $tag -> $dir/${tag}.traineddata ==="
}

for alphabet_name in "${!ALPHABETS[@]}"; do
  for font in "${FONT_LIST[@]}"; do
    train_one "$alphabet_name" "${ALPHABETS[$alphabet_name]}" "$font" \
      || echo "!!! $(date -Is) FAILED: $alphabet_name / $font (see log above) !!!"
  done
done

echo "=== $(date -Is) all training jobs attempted; see $WORK/*/*.traineddata ==="
