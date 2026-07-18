"""Measure per-character OCR reliability across font x size x engine x radix.

Renders N random lines drawn from a candidate alphabet into a PDF, rasterizes
each page, OCRs it with a registered `glyphive.restore.ocr` provider, and
reports which characters are safe to print. "Safe" means: the character is
read back with zero errors, AND no other character in the alphabet is ever
misread *into* it (a "CORRUPTING" confusion -- a silent wrong value, since the
target is a valid in-alphabet character and nothing downstream can tell the
misread happened). A misread whose target is NOT in the alphabet is
"alias-recoverable": it can be mapped back to the true source unambiguously
because the target could never have been printed on purpose.

The metric that matters is usable bytes per page, not raw character accuracy:

    usable_radix   = largest power of 2 <= len(safe)
    effective_bits = log2(usable_radix)              # 0 if len(safe) < 2
    chars_per_page = lines_per_page(size) * min(line_length, chars_per_line)
    bytes_per_page = chars_per_page * effective_bits / 8
    usable_bytes_per_page = bytes_per_page * (1 - line_insert_rate)

A length-mismatched line fails framing and becomes an erasure, so it contributes
zero usable bytes. Reports retain nominal `bytes_per_page` for geometry comparisons
but rank cells and presets by `usable_bytes_per_page`.

A smaller font fits more characters per page but is read worse, shrinking the
safe alphabet -- these two effects pull in opposite directions and only
measurement resolves them. `chars_per_line`/`lines_per_page` are computed from
actual font metrics (fpdf2's `get_string_width`) and US-Letter page geometry,
not assumed; a combination that would need to wrap a line is marked invalid
(reported as "WRAPS", no bogus density) rather than silently measured wrong.

Lines whose OCR length does not match the printed length (after stripping
spaces -- Tesseract's line-width-overflow noise) are a
distinct failure mode (insertion/deletion, not substitution) and are excluded
from per-character stats; their rate is reported separately as
`line_insert_rate`.

This tool has no dependency on glyphive's test suite; it depends only on
fpdf2, pypdfium2, and glyphive's own OCR provider registry.

Usage:
    python tools/ocr_font_report.py --font courier --engine tesseract \\
        --radix 16,32,64,85 --size 11 --dpi 300 --rows 60 [--json out.json]

    python tools/ocr_font_report.py --font courier --engine tesseract \\
        --size 8,9,11,12,14 --radix 16,32,64,85 --charset crockford32 --rows 60

    python tools/ocr_font_report.py --font courier --engine tesseract \\
        --radix 64,85 --extra-chars "*@#-^" --rows 60

    python tools/ocr_font_report.py --font ocr-b --engine tesseract \\
        --charset ABCDHKLMPRTVXY34 --size 6,8 \\
        --horizontal-alignment left,center,justify \\
        --character-spacing 0,0.1,0.2 --rows 60

    python tools/ocr_font_report.py --font "C:\\Windows\\Fonts\\consola.ttf" \\
        --engine tesseract --radix 32 --rows 60

Merge JSON reports from other machines/engines (e.g. the VM sweep) and
recompute the intersection-safe / dense / retype presets over the union:
    python tools/ocr_font_report.py --merge local.json vm.json --json merged.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from importlib.metadata import PackageNotFoundError, version

from pathlib_next import Path

# --- standard alphabets, so results are comparable to known encodings -------

_HEX16 = "0123456789ABCDEF"
_BASE32_RFC4648 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # excludes I L O U
_BASE64_RFC4648 = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
)
# Python's base64.b85encode alphabet (85 chars); used here as "base85".
_BASE85 = (
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz!#$%&()*+-;<=>?@^_`{|}~"
)
# ZeroMQ Z85 alphabet. Unlike Python's b85 alphabet, this is the symbol set
# meant by the report's standard Z85 comparison.
_Z85 = (
    "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ".-:+=^!/*?&<>()[]{}@%$#"
)
_PUNCTUATION_CANDIDATES = "*@#-^"

NAMED_CHARSETS: dict[str, str] = {
    "hex16": _HEX16,
    "base32": _BASE32_RFC4648,
    "crockford32": _CROCKFORD32,
    "base64": _BASE64_RFC4648,
    "base85": _BASE85,
    "z85": _Z85,
    "punctuation5": _PUNCTUATION_CANDIDATES,
}

_RADIX_TO_PRESET = {16: "hex16", 32: "base32", 64: "base64", 85: "z85"}

_CORE_FONTS = frozenset({"courier", "helvetica", "times", "symbol", "zapfdingbats", "arial"})

# US-Letter, matching src/glyphive/render/formats/pdf.py's margins (36pt).
_PAGE_W = 612.0
_PAGE_H = 792.0
_MARGIN = 36.0
_USABLE_W = _PAGE_W - 2 * _MARGIN
_USABLE_H = _PAGE_H - 2 * _MARGIN


def _largest_pow2_leq(n: int) -> int:
    if n <= 0:
        return 0
    return 1 << (n.bit_length() - 1)


def _append_unique(alphabet: str, extra_chars: str) -> str:
    """Append candidates without changing existing order or duplicating glyphs."""
    return "".join(dict.fromkeys(alphabet + extra_chars))


def _usable_bytes_per_page(result: dict) -> float | None:
    """Return erasure-adjusted capacity, including for older merged reports."""
    nominal = result.get("bytes_per_page")
    if nominal is None:
        return None
    recorded = result.get("usable_bytes_per_page")
    if recorded is not None:
        return float(recorded)
    erasure_rate = result.get("line_insert_rate")
    if erasure_rate is None:
        return None
    return float(nominal) * (1.0 - float(erasure_rate))


def _engine_version(engine: str) -> str | None:
    """Return the external OCR engine/package version for reproducibility."""
    if engine == "tesseract":
        try:
            completed = subprocess.run(
                ["tesseract", "--version"],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode == 0 and completed.stdout:
            first_line = completed.stdout.splitlines()[0].strip()
            return first_line.removeprefix("tesseract ")
        return None

    distribution = {"paddle": "paddleocr", "easyocr": "easyocr"}.get(engine)
    if distribution is None:
        return None
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


# --- rendering ---------------------------------------------------------------


def _resolve_font(pdf, font_arg: str, size: float) -> str:
    """Register a TTF path via add_font, or use a PDF core font name.

    Returns a display label for the font (basename for TTF, name for core).
    """
    # Consult the renderer's bundled-font registry rather than hardcoding one
    # font, so every font the PDF renderer bundles (ocr-b, dejavu-sans-mono, …)
    # is measurable here without editing this tool for each new addition.
    from glyphive.render.formats.pdf import _BUNDLED_FONTS

    lowered = font_arg.lower()
    if lowered in _BUNDLED_FONTS:
        from importlib import resources

        package, filename = _BUNDLED_FONTS[lowered]
        resource = resources.files(package).joinpath(filename)
        with resources.as_file(resource) as bundled:
            pdf.add_font(lowered, "", str(bundled))
        pdf.set_font(lowered, size=size)
        return f"{filename} (bundled)"

    candidate = Path(font_arg)
    if candidate.is_file():
        family = candidate.stem
        pdf.add_font(family, "", str(candidate))
        pdf.set_font(family, size=size)
        return candidate.name
    family = lowered
    if family not in _CORE_FONTS:
        raise ValueError(
            f"unsupported font {font_arg!r}: not a PDF core font "
            f"({', '.join(sorted(_CORE_FONTS))}), a bundled font "
            f"({', '.join(sorted(_BUNDLED_FONTS))}), or an existing file path"
        )
    pdf.set_font(family, size=size)
    return family


def font_geometry(
    font_arg: str,
    size: float,
    alphabet: str,
    character_spacing_pt: float = 0.0,
) -> tuple[int, int, float, str]:
    """Compute (chars_per_line, lines_per_page, max_char_width, font_label).

    chars_per_line is sized off the *widest* glyph in this alphabet at this
    font+size so a full-width line of any character combination still fits
    within the US-Letter usable width (matches the PDF renderer's margins).
    """
    if character_spacing_pt < 0:
        raise ValueError("character_spacing_pt must be >= 0")
    import fpdf

    pdf = fpdf.FPDF(orientation="P", unit="pt", format="Letter")
    pdf.set_margins(_MARGIN, _MARGIN)
    label = _resolve_font(pdf, font_arg, size)
    max_w = max(pdf.get_string_width(c) for c in alphabet)
    # small safety margin for kerning/rounding
    chars_per_line = max(
        1,
        int(((_USABLE_W * 0.98) + character_spacing_pt) // (max_w + character_spacing_pt)),
    )
    leading = size * 1.2
    lines_per_page = max(1, int(_USABLE_H // leading))
    return chars_per_line, lines_per_page, max_w, label


def render_lines(
    lines: list[str],
    font_arg: str,
    size: float,
    out_pdf: Path,
    *,
    horizontal_alignment: str = "left",
    character_spacing_pt: float = 0.0,
) -> str:
    """Render lines into a PDF, one per row, auto-paginating. Returns font label."""
    import fpdf

    pdf = fpdf.FPDF(orientation="P", unit="pt", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=_MARGIN)
    pdf.set_margins(_MARGIN, _MARGIN)
    label = _resolve_font(pdf, font_arg, size)
    leading = size * 1.2
    pdf.add_page()
    from glyphive.render.formats.pdf import (
        _fitted_font_size,
        _line_character_spacing,
    )

    for line in lines:
        pdf.set_x(_MARGIN)
        raw_width = pdf.get_string_width(line)
        spacing = _line_character_spacing(
            line,
            alignment=horizontal_alignment,
            base_spacing_pt=character_spacing_pt,
            text_width=raw_width,
            available_width=_USABLE_W,
        )
        line_size = _fitted_font_size(
            size,
            raw_width,
            _USABLE_W,
            character_spacing_pt=spacing,
            character_count=len(line),
        )
        if line_size != size:
            pdf.set_font_size(line_size)
        pdf.set_char_spacing(spacing)
        pdf.cell(
            w=_USABLE_W,
            h=leading,
            text=line,
            align="C" if horizontal_alignment == "center" else "L",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_char_spacing(0)
        if line_size != size:
            pdf.set_font_size(size)
    out_pdf.write_bytes(bytes(pdf.output()))
    return label


def rasterize_and_ocr(
    pdf_path: Path,
    dpi: int,
    engine: str,
    scratch: Path,
    *,
    alphabet: str,
    tesseract_constrained: bool,
) -> list[str]:
    """Rasterize every page at dpi and OCR each, concatenating lines in order."""
    import pypdfium2

    from glyphive.restore import ocr as glyphive_ocr

    provider = glyphive_ocr.get(engine)
    doc = pypdfium2.PdfDocument(str(pdf_path))
    all_lines: list[str] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            bitmap = page.render(scale=dpi / 72)
            image = bitmap.to_pil().convert("L")
            png_path = scratch / f"page{i:03d}.png"
            image.save(png_path)
            if engine == "tesseract" and tesseract_constrained:
                import pytesseract

                config = (
                    "--psm 6 "
                    f"-c tessedit_char_whitelist={alphabet} "
                    "-c load_system_dawg=0 -c load_freq_dawg=0"
                )
                text = pytesseract.image_to_string(image, config=config)
                all_lines.extend(line for line in text.splitlines() if line.strip())
            else:
                all_lines.extend(provider.ocr_image(png_path))
    finally:
        doc.close()
    return all_lines


# --- measurement ---------------------------------------------------------------


def measure(
    alphabet: str,
    font_arg: str,
    engine: str,
    dpi: int,
    size: float,
    rows: int,
    line_length_override: int | None,
    seed: int,
    scratch: Path,
    tesseract_constrained: bool = False,
    horizontal_alignment: str = "left",
    character_spacing_pt: float = 0.0,
) -> dict:
    if horizontal_alignment not in {"left", "center", "justify"}:
        raise ValueError("horizontal_alignment must be left, center, or justify")
    if character_spacing_pt < 0:
        raise ValueError("character_spacing_pt must be >= 0")
    chars_per_line, lines_per_page, max_w, font_label = font_geometry(
        font_arg, size, alphabet, character_spacing_pt
    )
    wraps = False
    if line_length_override is not None:
        line_length = line_length_override
        tracked_width = line_length * max_w + max(0, line_length - 1) * character_spacing_pt
        if tracked_width > _USABLE_W * 0.98:
            wraps = True
    else:
        line_length = chars_per_line

    rng = random.Random(seed)
    printed_rows = [
        "".join(rng.choice(alphabet) for _ in range(line_length)) for _ in range(rows)
    ]

    result_base = {
        "font_arg": font_arg,
        "font_label": font_label,
        "engine": engine,
        "alphabet": alphabet,
        "alphabet_len": len(alphabet),
        "dpi": dpi,
        "size": size,
        "rows": rows,
        "line_length": line_length,
        "chars_per_line": chars_per_line,
        "lines_per_page": lines_per_page,
        "seed": seed,
        "wraps": wraps,
        "tesseract_constrained": tesseract_constrained,
        "horizontal_alignment": horizontal_alignment,
        "character_spacing_pt": character_spacing_pt,
    }

    if wraps:
        # Geometrically guaranteed to wrap and destroy the frame -- don't
        # burn an OCR pass measuring a combination we already know is invalid.
        result_base.update(
            {
                "line_insert_rate": None,
                "length_mismatches": None,
                "per_char": {},
                "safe": "",
                "safe_len": 0,
                "usable_radix": 0,
                "effective_bits": 0.0,
                "nominal_bits": math.log2(len(alphabet)) if alphabet else 0.0,
                "efficiency": 0.0,
                "corrupting_pairs": [],
                "chars_per_page": min(line_length, chars_per_line) * lines_per_page,
                "bytes_per_page": None,
                "usable_bytes_per_page": None,
            }
        )
        return result_base

    pdf_path = scratch / "sample.pdf"
    render_lines(
        printed_rows,
        font_arg,
        size,
        pdf_path,
        horizontal_alignment=horizontal_alignment,
        character_spacing_pt=character_spacing_pt,
    )
    ocr_lines = rasterize_and_ocr(
        pdf_path,
        dpi,
        engine,
        scratch,
        alphabet=alphabet,
        tesseract_constrained=tesseract_constrained,
    )

    total: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    misreads: dict[str, Counter[str]] = defaultdict(Counter)
    length_mismatches = 0

    for i, printed in enumerate(printed_rows):
        ocr_line_raw = ocr_lines[i] if i < len(ocr_lines) else ""
        # A line-width overflow makes Tesseract insert spurious interior
        # spaces. The payload alphabet contains no spaces, so an interior space
        # is provably OCR noise. None of our
        # sample alphabets contain a space, so strip it before comparing
        # lengths -- that isolates genuine character insertion/deletion
        # (a dropped or duplicated *character*) from the separately-tracked
        # spurious-space framing bug.
        ocr_line = ocr_line_raw.replace(" ", "")
        if len(ocr_line) != len(printed):
            length_mismatches += 1
            continue
        for pos, pc in enumerate(printed):
            oc = ocr_line[pos]
            total[pc] += 1
            if oc != pc:
                errors[pc] += 1
                misreads[pc][oc] += 1

    line_insert_rate = length_mismatches / rows if rows else 0.0

    alphabet_set = set(alphabet)
    per_char = {}
    corrupting_targets: set[str] = set()
    for c in alphabet:
        n = total[c]
        err = errors[c]
        rate = (err / n) if n else None
        top_misreads = misreads[c].most_common(5)
        classified = []
        for target, count in top_misreads:
            corrupting = target in alphabet_set
            if corrupting:
                corrupting_targets.add(target)
            classified.append({"target": target, "count": count, "corrupting": corrupting})
        per_char[c] = {"samples": n, "errors": err, "error_rate": rate, "misreads": classified}

    safe = [
        c for c in alphabet if total[c] > 0 and errors[c] == 0 and c not in corrupting_targets
    ]

    usable_radix = _largest_pow2_leq(len(safe))
    effective_bits = math.log2(usable_radix) if usable_radix > 0 else 0.0
    nominal_bits = math.log2(len(alphabet)) if len(alphabet) > 0 else 0.0
    efficiency = effective_bits / nominal_bits if nominal_bits > 0 else 0.0

    chars_per_page = min(line_length, chars_per_line) * lines_per_page
    bytes_per_page = chars_per_page * effective_bits / 8
    usable_bytes_per_page = bytes_per_page * (1.0 - line_insert_rate)

    corrupting_pairs = []
    for c, info in per_char.items():
        for m in info["misreads"]:
            if m["corrupting"]:
                corrupting_pairs.append((c, m["target"], m["count"]))

    result_base.update(
        {
            "line_insert_rate": line_insert_rate,
            "length_mismatches": length_mismatches,
            "per_char": per_char,
            "safe": "".join(safe),
            "safe_len": len(safe),
            "usable_radix": usable_radix,
            "effective_bits": effective_bits,
            "nominal_bits": nominal_bits,
            "efficiency": efficiency,
            "corrupting_pairs": corrupting_pairs,
            "chars_per_page": chars_per_page,
            "bytes_per_page": bytes_per_page,
            "usable_bytes_per_page": usable_bytes_per_page,
        }
    )
    return result_base


# --- presets -------------------------------------------------------------------


def compute_presets(results: list[dict]) -> dict:
    """Derive `safe` (cross-engine intersection), `dense`, and `retype` presets.

    Presets are data derived from the matrix, not new code paths.
    """
    valid = [r for r in results if _usable_bytes_per_page(r) is not None]
    engines_present = sorted({r["engine"] for r in results})

    presets: dict = {"engines_present": engines_present}

    # --- dense: best single cell by erasure-adjusted usable bytes per page.
    if valid:
        dense = max(valid, key=lambda r: _usable_bytes_per_page(r) or 0.0)
        presets["dense"] = {
            "font": dense["font_label"],
            "size": dense["size"],
            "charset": dense.get("charset_label"),
            "engine": dense["engine"],
            "horizontal_alignment": dense.get("horizontal_alignment", "left"),
            "character_spacing_pt": dense.get("character_spacing_pt", 0.0),
            "safe": dense["safe"],
            "safe_len": dense["safe_len"],
            "usable_radix": dense["usable_radix"],
            "bytes_per_page": dense["bytes_per_page"],
            "usable_bytes_per_page": _usable_bytes_per_page(dense),
            "line_insert_rate": dense.get("line_insert_rate"),
            "note": "commits the document to this engine class; human-retype fallback still works",
        }
    else:
        presets["dense"] = None

    # --- safe: intersection-safe set across every engine present, for cells
    # measured under all of them (same font/size/charset key on each engine).
    by_key: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for r in valid:
        key = (
            r["font_label"],
            r["size"],
            r.get("charset_label"),
            r["alphabet"],
            r.get("dpi"),
            r.get("line_length"),
            r.get("horizontal_alignment", "left"),
            r.get("character_spacing_pt", 0.0),
        )
        by_key[key][r["engine"]] = r

    intersection_candidates = []
    for key, per_engine in by_key.items():
        if set(per_engine) != set(engines_present):
            continue  # not measured on every engine present; can't intersect fairly
        safe_sets = [set(r["safe"]) for r in per_engine.values()]
        inter = set.intersection(*safe_sets) if safe_sets else set()
        usable_radix = _largest_pow2_leq(len(inter))
        effective_bits = math.log2(usable_radix) if usable_radix > 0 else 0.0
        any_r = next(iter(per_engine.values()))
        chars_per_page = any_r["chars_per_page"]
        bytes_per_page = chars_per_page * effective_bits / 8
        # The cross-engine preset promises portability, so rank it using the
        # worst measured line-erasure rate among the engines in this cell.
        line_insert_rate = max(
            float(r.get("line_insert_rate") or 0.0) for r in per_engine.values()
        )
        usable_bytes_per_page = bytes_per_page * (1.0 - line_insert_rate)
        intersection_candidates.append(
            {
                "font": key[0],
                "size": key[1],
                "charset": key[2],
                "engines": engines_present,
                "horizontal_alignment": key[6],
                "character_spacing_pt": key[7],
                "safe": "".join(sorted(inter)),
                "safe_len": len(inter),
                "usable_radix": usable_radix,
                "bytes_per_page": bytes_per_page,
                "usable_bytes_per_page": usable_bytes_per_page,
                "line_insert_rate": line_insert_rate,
            }
        )
    if intersection_candidates:
        presets["safe"] = max(
            intersection_candidates, key=lambda c: c["usable_bytes_per_page"]
        )
    else:
        presets["safe"] = (
            "no (font,size,charset) cell was measured on every engine in "
            f"{engines_present}; run the same combos on each engine and re-merge"
        )

    # --- retype: optimize for a human transcriber, not OCR -- prefer a larger
    # size (easier to read/write by hand) and a maximally visually-distinct
    # safe set (proxied here by safe_len) over raw bytes_per_page. Heuristic,
    # not a bang-for-buck ranking; documented as such.
    if valid:
        retype = max(valid, key=lambda r: (r["size"], r["safe_len"]))
        presets["retype"] = {
            "font": retype["font_label"],
            "size": retype["size"],
            "charset": retype.get("charset_label"),
            "engine": retype["engine"],
            "horizontal_alignment": retype.get("horizontal_alignment", "left"),
            "character_spacing_pt": retype.get("character_spacing_pt", 0.0),
            "safe": retype["safe"],
            "safe_len": retype["safe_len"],
            "note": "heuristic: largest size, most visually-distinct safe set; "
            "not a bytes_per_page ranking",
        }
    else:
        presets["retype"] = None

    return presets


# --- reporting ---------------------------------------------------------------


def print_report(results: list[dict]) -> None:
    def sort_key(r: dict) -> tuple:
        usable_bpp = _usable_bytes_per_page(r)
        return (
            usable_bpp is not None,
            usable_bpp if usable_bpp is not None else -1,
        )

    ranked = sorted(results, key=sort_key, reverse=True)
    header = (
        f"{'font':<14} {'size':>4} {'engine':<10} {'align':<7} {'track':>5} "
        f"{'charset':<12} {'len(alph)':>9} "
        f"{'len(safe)':>9} {'radix':>5} {'eff_bits':>8} {'chars/ln':>8} {'lines/pg':>8} "
        f"{'nom_B/pg':>9} {'use_B/pg':>9} {'ins_rate':>8}"
    )
    print(header)
    print("-" * len(header))
    for r in ranked:
        bpp = r.get("bytes_per_page")
        bpp_s = "WRAPS" if r.get("wraps") else (f"{bpp:.0f}" if bpp is not None else "n/a")
        usable_bpp = _usable_bytes_per_page(r)
        usable_bpp_s = f"{usable_bpp:.0f}" if usable_bpp is not None else "n/a"
        ins = r.get("line_insert_rate")
        ins_s = f"{ins * 100:.1f}%" if ins is not None else "n/a"
        print(
            f"{r['font_label']:<14} {r['size']:>4} {r['engine']:<10} "
            f"{r.get('horizontal_alignment', 'left'):<7} "
            f"{r.get('character_spacing_pt', 0.0):>5.2f} "
            f"{r['charset_label']:<12} {r['alphabet_len']:>9} {r['safe_len']:>9} "
            f"{r['usable_radix']:>5} {r['effective_bits']:>8.2f} {r['chars_per_line']:>8} "
            f"{r['lines_per_page']:>8} {bpp_s:>9} {usable_bpp_s:>9} {ins_s:>8}"
        )
    print()
    for r in ranked:
        if r.get("wraps"):
            print(f"=== {r['font_label']} {r['size']}pt / {r['engine']} / {r['charset_label']} === WRAPS (invalid, not measured)")
            continue
        print(f"=== {r['font_label']} {r['size']}pt / {r['engine']} / {r['charset_label']} ===")
        print(f"  safe ({r['safe_len']}): {r['safe']}")
        if r["corrupting_pairs"]:
            print("  CORRUPTING confusions (source -> target, count):")
            for c, target, count in sorted(r["corrupting_pairs"], key=lambda t: -t[2]):
                print(f"    {c!r} -> {target!r}  x{count}")
        else:
            print("  CORRUPTING confusions: none")
        print()


def print_presets(presets: dict) -> None:
    print("=== presets ===")
    print(f"engines present: {presets['engines_present']}")
    for name in ("safe", "dense", "retype"):
        print(f"-- {name} --")
        val = presets[name]
        if isinstance(val, str):
            print(f"  {val}")
        elif val is None:
            print("  no valid (non-wrapping) cell to choose from")
        else:
            print(f"  {json.dumps(val, indent=2)}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", help="PDF core font name or path to .ttf")
    parser.add_argument(
        "--engine", help="comma-separated registered glyphive.restore.ocr provider name(s)"
    )
    parser.add_argument(
        "--radix",
        default="",
        help="comma-separated 16,32,64,85 (hex, RFC 4648 base32/base64, Z85)",
    )
    parser.add_argument(
        "--charset",
        default="",
        help=(
            "comma-separated preset names (e.g. crockford32, z85, punctuation5) "
            "or literal charsets"
        ),
    )
    parser.add_argument(
        "--extra-chars",
        default="",
        help=(
            "append candidate glyphs to every requested radix/charset, preserving "
            "order and removing duplicates (e.g. '*@#-^')"
        ),
    )
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--size", default="11", help="comma-separated list of font sizes (pt)")
    parser.add_argument(
        "--rows", type=int, default=60, help="random rows per cell (default: 60)"
    )
    parser.add_argument(
        "--line-length",
        type=int,
        default=None,
        help="override the auto-computed (from font metrics) chars-per-line",
    )
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument(
        "--horizontal-alignment",
        default="left",
        help="comma-separated left,center,justify layout cells (default: left)",
    )
    parser.add_argument(
        "--character-spacing",
        default="0",
        help="comma-separated nonnegative extra tracking values in points (default: 0)",
    )
    parser.add_argument(
        "--tesseract-constrained",
        action="store_true",
        help=(
            "for Tesseract cells, whitelist the candidate alphabet and disable "
            "the system/frequency dictionaries"
        ),
    )
    parser.add_argument("--json", default=None, help="path to write full JSON report")
    parser.add_argument(
        "--merge",
        nargs="+",
        default=None,
        help="merge these JSON report files instead of measuring; recompute presets over the union",
    )
    args = parser.parse_args(argv)

    if args.merge:
        results: list[dict] = []
        for path in args.merge:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            results.extend(data["results"] if isinstance(data, dict) else data)
        print_report(results)
        presets = compute_presets(results)
        print_presets(presets)
        if args.json:
            Path(args.json).write_text(
                json.dumps({"results": results, "presets": presets}, indent=2),
                encoding="utf-8",
            )
            print(f"wrote JSON report to {args.json}")
        return 0

    if not args.font or not args.engine:
        raise SystemExit("--font and --engine are required unless --merge is used")

    combos: list[tuple[str, str]] = []  # (label, alphabet)
    if args.radix:
        for tok in args.radix.split(","):
            tok = tok.strip()
            if not tok:
                continue
            radix = int(tok)
            if radix not in _RADIX_TO_PRESET:
                raise SystemExit(f"unsupported --radix value {radix}; use 16,32,64,85")
            preset = _RADIX_TO_PRESET[radix]
            base_alphabet = NAMED_CHARSETS[preset]
            alphabet = _append_unique(base_alphabet, args.extra_chars)
            label = f"{preset}+extra" if len(alphabet) != len(base_alphabet) else preset
            combos.append((label, alphabet))
    if args.charset:
        for tok in args.charset.split(","):
            tok = tok.strip()
            if not tok:
                continue
            if tok in NAMED_CHARSETS:
                base_alphabet = NAMED_CHARSETS[tok]
                alphabet = _append_unique(base_alphabet, args.extra_chars)
                label = f"{tok}+extra" if len(alphabet) != len(base_alphabet) else tok
                combos.append((label, alphabet))
            else:
                alphabet = _append_unique(tok, args.extra_chars)
                label = "custom+extra" if len(alphabet) != len(tok) else "custom"
                combos.append((label, alphabet))
    if not combos:
        raise SystemExit("must specify at least one of --radix or --charset")

    sizes = [float(tok.strip()) for tok in args.size.split(",") if tok.strip()]
    alignments = [
        tok.strip() for tok in args.horizontal_alignment.split(",") if tok.strip()
    ]
    invalid_alignments = sorted(set(alignments) - {"left", "center", "justify"})
    if not alignments:
        raise SystemExit("--horizontal-alignment must include at least one value")
    if invalid_alignments:
        raise SystemExit(
            "unsupported --horizontal-alignment value(s) %s; use left,center,justify"
            % ",".join(invalid_alignments)
        )
    character_spacings = [
        float(tok.strip()) for tok in args.character_spacing.split(",") if tok.strip()
    ]
    if not character_spacings or any(value < 0 for value in character_spacings):
        raise SystemExit("--character-spacing values must be nonnegative")
    requested_engines = [tok.strip() for tok in args.engine.split(",") if tok.strip()]

    if not shutil_which("tesseract") and "tesseract" in requested_engines:
        os.environ["PATH"] = (
            r"C:\Program Files\Tesseract-OCR" + os.pathsep + os.environ.get("PATH", "")
        )

    from glyphive.restore import ocr as glyphive_ocr

    engines = []
    for name in requested_engines:
        try:
            provider = glyphive_ocr.get(name)
        except Exception as exc:
            print(f"warning: engine {name!r} is not registered ({exc}); skipping", file=sys.stderr)
            continue
        if not provider.is_available():
            print(f"warning: engine {name!r} is not available on this machine; skipping", file=sys.stderr)
            continue
        engines.append(name)
    if not engines:
        raise SystemExit(f"none of the requested engines {requested_engines} are available here")

    results = []
    with tempfile.TemporaryDirectory(prefix="ocr_font_report_") as tmp:
        scratch = Path(tmp)
        for engine in engines:
            for size in sizes:
                for horizontal_alignment in alignments:
                    for character_spacing_pt in character_spacings:
                        for label, alphabet in combos:
                            r = measure(
                                alphabet=alphabet,
                                font_arg=args.font,
                                engine=engine,
                                dpi=args.dpi,
                                size=size,
                                rows=args.rows,
                                line_length_override=args.line_length,
                                seed=args.seed,
                                scratch=scratch,
                                tesseract_constrained=args.tesseract_constrained,
                                horizontal_alignment=horizontal_alignment,
                                character_spacing_pt=character_spacing_pt,
                            )
                            r["charset_label"] = label
                            r["engine_version"] = _engine_version(engine)
                            results.append(r)

    print_report(results)
    presets = compute_presets(results)
    print_presets(presets)

    if args.json:
        Path(args.json).write_text(
            json.dumps({"results": results, "presets": presets}, indent=2), encoding="utf-8"
        )
        print(f"wrote JSON report to {args.json}")

    return 0


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    raise SystemExit(main())
