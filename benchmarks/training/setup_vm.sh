#!/usr/bin/env bash
# One-time VM setup for Tesseract LSTM training (Rocky 9).
#
# Rocky 9 ships tesseract 4.1.1 with NO training tools and NO leptonica-devel,
# so both leptonica and tesseract 5.4.1 (with training tools) are built from
# source. After this runs, train_ocr_models.py (the actual per-font/per-
# alphabet training driver) can be used directly.
#
# Everything is idempotent; safe to re-run. Building tesseract 5 from source
# also gives a version to compare against the OS-packaged 4.1.1.
set -euo pipefail

ROOT="/root/glyphive-ocr-training"
mkdir -p "$ROOT"
cd "$ROOT"

export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:/usr/local/lib64/pkgconfig
export LD_LIBRARY_PATH=/usr/local/lib:/usr/local/lib64
export PATH=/usr/local/bin:$PATH

echo "=== $(date -Is) VM training setup ==="

# --- 1. Build/toolchain + all -devel packages the training tools require ----
# NB: dnf aborts the WHOLE transaction if any single package is missing, so
# install in groups that are all known to exist in Rocky 9 + EPEL. Training
# tools need pango/cairo/icu dev headers (checked at ./configure time); tiff/
# jpeg/webp dev headers are needed by leptonica for text2image to WRITE images.
dnf install -y gcc-c++ make cmake autoconf automake libtool pkgconfig git \
  libpng-devel zlib-devel libicu-devel
dnf install -y pango-devel cairo-devel
dnf install -y libtiff-devel libjpeg-turbo-devel libwebp-devel
dnf install -y liberation-mono-fonts

# --- 2. Leptonica from source (with tiff/jpeg/png/webp) ---------------------
if ! (PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:/usr/local/lib64/pkgconfig \
      pkg-config --exists lept); then
  echo "--- building leptonica ---"
  [ -d leptonica ] || git clone --depth 1 --branch 1.85.0 \
    https://github.com/DanBloomberg/leptonica.git
  cd leptonica
  [ -f ./configure ] || { ./autobuild 2>/dev/null || autoreconf -fi; }
  ./configure --prefix=/usr/local
  make -j"$(nproc)"
  make install
  ldconfig
  cd "$ROOT"
fi

# --- 3. Tesseract 5.4.1 with training tools --------------------------------
if ! command -v text2image >/dev/null 2>&1 || ! command -v lstmtraining >/dev/null 2>&1; then
  echo "--- building tesseract 5.4.1 + training tools ---"
  [ -d tesseract ] || git clone --depth 1 --branch 5.4.1 \
    https://github.com/tesseract-ocr/tesseract.git
  cd tesseract
  if ! pkg-config --exists icu-uc icu-i18n pango cairo pangocairo pangoft2; then
    echo "FATAL: training-tool deps (icu/pango/cairo dev) missing" >&2
    exit 1
  fi
  [ -f ./configure ] || ./autogen.sh
  ./configure --prefix=/usr/local
  make -j"$(nproc)"
  make install
  make training -j"$(nproc)"
  make training-install
  ldconfig
  cd "$ROOT"
fi

echo "--- tool versions ---"
text2image --version 2>&1 | head -2
lstmtraining --version 2>&1 | head -2

# --- 4. Register the bundled OCR-B font with fontconfig --------------------
# text2image resolves --font by fontconfig family name, not by --fonts_dir
# alone, so the font must be installed system-wide and the cache refreshed.
mkdir -p /usr/share/fonts/glyphive
cp /root/glyphive_ci/src/glyphive/assets/fonts/ocr_b/OCR-B.ttf /usr/share/fonts/glyphive/
fc-cache -f /usr/share/fonts/glyphive
fc-list | grep -i ocr-b || { echo "FATAL: OCR-B font not registered" >&2; exit 1; }

# --- 5. tessdata_best/eng + the training configs ---------------------------
# The best-model tarball is JUST the model; the lstm.train config used to
# generate .lstmf files lives in the tesseract source tree's tessdata/configs.
TESSDATA_BEST="$ROOT/tessdata_best"
mkdir -p "$TESSDATA_BEST"
[ -f "$TESSDATA_BEST/eng.traineddata" ] || curl -sL \
  -o "$TESSDATA_BEST/eng.traineddata" \
  https://github.com/tesseract-ocr/tessdata_best/raw/main/eng.traineddata
[ -d "$TESSDATA_BEST/configs" ] || \
  cp -r "$ROOT/tesseract/tessdata/configs" "$TESSDATA_BEST/"
[ -d "$TESSDATA_BEST/tessconfigs" ] || \
  cp -r "$ROOT/tesseract/tessdata/tessconfigs" "$TESSDATA_BEST/" 2>/dev/null || true

echo "=== $(date -Is) setup complete; run train_ocr_models.py to train ==="
