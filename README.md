# glyphive

Archive an arbitrary file tree to a **compact, OCR-friendly, printable** page
format (plain text, PDF, or Word) and restore it from a scan — or from a re-typed
transcript when OCR fails. Think "QR-code paper backup", but the pages stay
human-legible and human-re-typeable, and you are not at the mercy of a phone
camera's QR decoder.

> **Status: alpha, under active development.** The format and CLI may still change.

## Why not just base64-on-paper?

Because we tried, and recovering it took three OCR engines and days of manual
pixel-verification — and still stalled. The default `g1` codec is designed
specifically to avoid that failure:

- a **confusable-free alphabet** (Crockford Base32 — no `0/O`, `1/l/I`, `U`),
- a **per-line check character** so a bad line is caught and localized immediately,
  without decoding anything downstream,
- **per-page Reed-Solomon parity** so small OCR errors *self-heal* instead of
  corrupting everything after them,
- a **per-page hash + page numbers** so missing / out-of-order / corrupt pages are
  obvious before assembly.

See `AGENTS.md` in the repository for the full recovery postmortem that motivates
each of these.

## Install

```bash
pip install glyphive            # text output only
pip install "glyphive[pdf]"     # + PDF rendering
pip install "glyphive[docx]"    # + Word (.docx) rendering
pip install "glyphive[all]"     # everything, incl. zstd + OCR helpers
```

## Usage (tar/bsdtar-like)

```bash
# Create an OCR-friendly archive page set (defaults chosen for best density+safety)
glyphive create -f backup.pdf --format pdf -C project .

# Restore from a re-typed / OCR'd text transcript
glyphive extract -f backup.txt -C restored

# Inspect the header/manifest without extracting
glyphive list -f backup.txt
```

<!-- Full format spec (header grammar, line frame, alphabet) documented at P9. -->
