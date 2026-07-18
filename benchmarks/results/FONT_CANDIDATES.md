# Font and OCR model candidate ledger

This ledger records the exact artifacts considered for Glyphive's printed
channel. It separates provenance from measured OCR behavior: a font's name,
license, or standards ancestry is not evidence that it improves recovery.
Measurements below are diagnostic Rocky Linux VM runs, not CI performance
claims. A recommendation still requires an end-to-end byte-for-byte restore
gate on the intended printer/scanner or rasterization path.

## Font candidates

| Candidate | Pinned artifact | License and bundling decision | Evaluation status |
| --- | --- | --- | --- |
| OCR-B by Raisty | [`jaycee723/ocr-b` commit `fedeba8`](https://github.com/jaycee723/ocr-b/tree/fedeba81519770109925b5bec70e940be5948d8f); `OCR-B.ttf`, 36,780 bytes, SHA-256 `367d876cca948ecd4900851f6e85687cbb6e71de9d0d2f36348edec5655526af` | [SIL OFL 1.1](https://github.com/jaycee723/ocr-b/blob/fedeba81519770109925b5bec70e940be5948d8f/OFL.txt), Reserved Font Name `OCR-B`; the unmodified file and license are bundled | Measured; see below |
| OCRAB hybrid | [`smallwat3r/ocrab-font` commit `9a06a45`](https://github.com/smallwat3r/ocrab-font/tree/9a06a45c7571adc071b506605beb2a9a4ba29eca); `ocrab.ttf`, 27,184 bytes, SHA-256 `3bc94e4b92388fbed7a6b4be9713f70c1c07a70911a7520e4a0c948dd5f91e5d` | [SIL OFL 1.1](https://github.com/smallwat3r/ocrab-font/blob/9a06a45c7571adc071b506605beb2a9a4ba29eca/LICENSE); evaluated externally, not bundled | Measured and rejected for the current channel. This is intentionally a new OCR-A/OCR-B hybrid, not an ISO OCR-B size or style |
| OCRA repository fonts | [`bcssupp0rt/ocrafont` commit `c6d0c8b`](https://github.com/bcssupp0rt/ocrafont/tree/c6d0c8bae5fe4d0da46eeb43d0f61f5f21b77974); `ocra.ttf` SHA-256 `7b85eb41528147dd4aa8f697b6bbc1656163e937f54cd71d64b541823d2a1725`; `OCRAII.TTF` `8cbc3c09199e3a6d94c619cffadde4776dfd53eb8760519e1083f4ade093d61e`; `ocraI.ttf` `c9dd24ad539197486544034f571e31c9d06ddf18d0edea060ca13da19b7c7695`; `ocraIII.ttf` `4336316f2e69d9db121e0662dfecb5edc19c8da04798aa466b4db3cb0cb52f3d`; `ocraIV.ttf` `e1e683b83d0cf53956f186f68374bcb995455b97e99a4e047eecad9ce17fdbcc` | [GPL-3.0 repository](https://github.com/bcssupp0rt/ocrafont/blob/c6d0c8bae5fe4d0da46eeb43d0f61f5f21b77974/LICENSE); evaluated externally, not bundled | All five measured files rejected. The numbered variants are OCR-A artifacts, not ISO OCR-B sizes |
| Tsukurimashou OCR fonts | [Tsukurimashou 0.3.1 ZIP](https://tsukurimashou.org/files/ocr-0.3.1.zip), SHA-256 `58136fccfdee0923cc83a20996a067b98bae054570ee41bf896d7ca8224399bf`; `OCRB.ttf` SHA-256 `67b11c470222c7bb4550e7d4c216fd06145a939208af77e5f946bcee53e70868`; Sharp `OCRBS.ttf` SHA-256 `29587e27376566463c4d5a1b8dfb7792fde91cb1261b511fa10f65aed8c1f354` | The source package carries multiple notices and embedding terms that require file-by-file review; evaluated externally and not bundled | Both measured and passed synthetic PDF/raster restore gates; see below |
| DejaVu Sans Mono | [`dejavu-fonts` release `version_2_37`](https://github.com/dejavu-fonts/dejavu-fonts/releases/tag/version_2_37); `ttf/DejaVuSansMono.ttf`, 340,712 bytes, SHA-256 `b4a6c3e4faab8773f4ff761d56451646409f29abedd68f05d38c2df667d3c582` | [DejaVu Fonts License](https://github.com/dejavu-fonts/dejavu-fonts/blob/version_2_37/LICENSE) (Bitstream Vera + Arev; permissive, allows embedding/bundling); the unmodified file and license are **bundled** as an opt-in PDF font. Note the ~335 KB size (~9× OCR-B) added to the wheel for this asset | Bundled as an available option; diagnostic evidence recorded (one of only two fonts, with Courier, that held up under `tesseract-glyphive` in the 2026-07-17 scan/PDF restore findings — strong at 8–12 pt, 26 OK/40 at blur 1.0). **End-to-end restore gate PASSED** 2026-07-17 on the VM: `create --font dejavu-sans-mono --font-size 8` → rasterize 300 DPI → `extract --ocr-engine tesseract-glyphive` (auto 0.6 de-scan) → `diff -r` byte-for-byte identical (2 files, 2 pages; one page's header was OCR-missing and recovered by document-wide RS). Promoted to the shipped default font 2026-07-18 (maintainer override favoring real-scan robustness over the synthetic density result; see the Default-font comparison below). Courier remains a zero-embedding, denser core-font option via `--font courier` |

The OCRA repository's filenames containing `I`, `III`, or `IV` are OCR-A
artifacts. Their names are not evidence of conformity to the OCR-B sizes in
ISO 1073-2, nor evidence that their stroke dimensions match those sizes.

## OCR-B styles and nominal sizes

[ISO 1073-2](https://www.iso.org/standard/5568.html) specifies OCR-B in two
primary design treatments. The constant-stroke-width form has substantially
rounded, constant-width stroke endings and is specified in Sizes I, III, and
IV. The letterpress form adapts stroke widths and uses squared or specially
shaped endings for letterpress reproduction; it is specified only in Size I.
These are physical print designs, not interchangeable font-weight labels.

| ISO size | Nominal character centerline height | Nominal stroke width | Scope |
| --- | ---: | ---: | --- |
| I | 0.094 in (2.4 mm) | 0.014 in (0.35 mm) | Constant-stroke-width and letterpress |
| III | 0.126 in (3.2 mm) | 0.015 in (0.38 mm) | Constant-stroke-width only |
| IV | 0.141 in (3.6 mm) | 0.020 in (0.50 mm) | Constant-stroke-width only |

A scalable TTF rendered at different point sizes does not automatically
reproduce these optical designs or nominal stroke widths. Future comparison
must record the actual font outline, point size, rendered pixel dimensions,
DPI, and measured stroke width. It should test both rounded/constant and
squared/letterpress-style endings when suitable licensed files can be sourced.

## Default-font comparison (2026-07-17, VM)

Comparative sweep to decide the default PDF font, via `tools/ocr_font_report.py`
on the VM (Rocky 9, Tesseract 4.1.1 + the `tesseract-glyphive` constrained
profile), charset `ABCDHKLMPRTVXY34`, radix 16, 300 DPI, 150 rows, the tool's
default page geometry. Raw JSON: `benchmarks/results/fontcmp-<font>_<engine>.json`.
Values are **usable bytes/page** (`bytes_per_page × (1 − line_insert_rate)`) at
the best configuration per cell; higher is better.

| font × engine | 6 pt | 8 pt | 10 pt | 11 pt | 12 pt |
| --- | ---: | ---: | ---: | ---: | ---: |
| courier × tesseract | 5366 | 4098 | 2622 | 2146 | 1825 |
| courier × tesseract-glyphive | **5439** | **4125** | **2640** | **2160** | 1825 |
| dejavu-sans-mono × tesseract | 5475 | 3004 | 1944 | 2090 | 1776 |
| dejavu-sans-mono × tesseract-glyphive | 5475 | 3045 | 1944 | 2105 | 1825 |
| ocr-b × tesseract | 3636 | 2024 | 1162 | 624 | 594 |
| ocr-b × tesseract-glyphive | 5050 | 2138 | 1745 | 1317 | 1125 |

**Decision: KEEP Courier as the default PDF font.** Applying the pre-set rule
(switch only if a bundled font beats Courier by ≥10% usable bytes/page on BOTH
engines *and* passes the restore gate): DejaVu's only edge is a ~0.7% margin at
6 pt (noise-level), and it *loses* by ~26–35% at 8 pt and every larger size;
OCR-B loses at every size. No bundled font clears the ≥10% bar. The embed-cost
tie-breaker also favors Courier — a PDF core font (0 KB embedded) vs DejaVu's
~340 KB per PDF. DejaVu and OCR-B remain measured, restore-verified **options**
(`--font dejavu-sans-mono` / `--font ocr-b`); they are not promoted to default.

**Override (2026-07-18): default set to `dejavu-sans-mono`.** The synthetic
`usable_bytes_per_page` sweep above says keep Courier, and that evidence stands
unchanged. The maintainer nonetheless chose DejaVu Sans Mono as the shipped
default, prioritizing **real-scan robustness over synthetic density**: on real
photographed scans (2026-07-17 findings) DejaVu was one of only two fonts that
held up under `tesseract-glyphive`, and it passed its byte-for-byte restore
gate — a real-world signal the rendered-row density metric does not capture.
The accepted costs are explicit: ~26 % lower usable density at 8 pt+ (more
pages per document) and ~340 KB embedded per PDF (vs Courier's zero-embedding
core font). Courier remains a first-class option (`--font courier`) and is the
denser choice for size-sensitive output; this is a values call favoring
recovery reliability, not a claim that DejaVu is denser.

## Verified diagnostic measurements

The cells below used 300 DPI randomized rows and Tesseract 4.1.1. The
`glyphive-safe` configuration used page segmentation mode 6, the exact
candidate-character whitelist, and disabled the system and frequency DAWGs.
The ordinary configuration did not impose those constraints.

| Font / model / size | Rows | Configuration | Safe subset | Radix | Insertions | Erasure-adjusted bytes/page | Decision |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| Courier / official `tessdata_fast` English / 8 pt | 150 | Ordinary | 16/16 | 16 | 0% | 2,250 | Current strongest unconstrained cell |
| Bundled OCR-B / `tessdata_fast` English / 6 pt | 150 | Ordinary | 15/16 | 8 | 4.67% | 2,145 | Not promoted: radix loss |
| Bundled OCR-B / `tessdata_best` English / 6 pt | 150 | Ordinary | 11/16 | 8 | 9.33% | 2,040 | Not promoted |
| Bundled OCR-B / standard `tessdata` English / 6 pt | 150 | Ordinary | 11/16 | 8 | 10% | 2,025 | Not promoted |
| Bundled OCR-B / `tessdata_fast` English / 6 pt | 150 | `glyphive-safe` | 16/16 | 16 | 0% | 3,000 | Promising experimental cell; full-frame restore gate pending |
| OCRAB / system and `tessdata_fast` English / 6--10 pt | 100 per size | Ordinary | 11--12/16 | 8 | 74--79% | at most 562.5 | Rejected; dominant `4` confusion plus heavy erasures |
| OCRA `ocra.ttf` / system and `tessdata_fast` English / 6--10 pt | 100 per size | Ordinary | 10--12/16 | 8 | 66--85% | at most 765 | Rejected; dominant `4` confusions plus heavy erasures |
| OCRA `OCRAII.TTF` / system and `tessdata_fast` English / 6--10 pt | 100 per size | Ordinary | 11--12/16 | 8 | 69--79% | at most 697.5 | Rejected |
| OCRAB / `tessdata_fast` English / 6 pt | 150 | `glyphive-safe` | 0/16 | 0 | 59.3% | 0 | Rejected |
| OCRA `ocra.ttf` / `tessdata_fast` English / 6 pt | 150 | `glyphive-safe` | 0/16 | 0 | 52% | 0 | Rejected |
| OCRA `ocraI.ttf` / `tessdata_fast` English / 6.0--10.2 pt | 100 per size | `glyphive-safe` | 0/16 | 0 | 58--74% | 0 | Rejected at every size |
| OCRA `ocraIII.ttf` / `tessdata_fast` English / 6.0--10.2 pt | 100 per size | `glyphive-safe` | 0/16 | 0 | 75--88% | 0 | Rejected at every size |
| OCRA `ocraIV.ttf` / `tessdata_fast` English / 6.0--10.2 pt | 100 per size | `glyphive-safe` | 0/16 | 0 | 100% | 0 | Rejected: every line erased |

The OCRA variant sweep used 6.0, 6.8, 9.1, and 10.2 pt only as a common
comparison scale near nominal OCR-B heights. It does not establish that these
OCR-A files implement ISO OCR-B Size I, III, or IV.

### Tsukurimashou 0.3.1 trial

The regular `OCRB.ttf` retained 16/16 symbols under the constrained profile;
its best measured size was 6.8 pt at 4,708 usable bytes/page. The Sharp-outline
`OCRBS.ttf` retained 16/16 symbols with zero erased rows at 6 pt and measured
6,050 usable bytes/page in the character-grid geometry. Both candidates then
passed complete 12,036-byte Zstandard PDF -> 300 DPI raster -> constrained
Tesseract -> restore gates byte-for-byte.

The 6,050-byte figure is diagnostic geometry, not current product throughput.
Glyphive's codec still emits fixed 60-character rows, so both restored trials
occupied the same four PDF pages; the Sharp font's wider measured row capacity
is not yet consumed by the wire renderer.

The 0.3.1 package documentation and the project's current 0.4pre
[design notes](https://tsukurimashou.org/ocr.pdf) both warn against treating
scalable point sizes as real OCR-B optical sizes: the supplied sizes are linear
scaling of outlines. The notes also describe overlapping outlines in the Sharp
variant and state that it is not suitable for OCR. Its successful synthetic
gate is retained as an experimental result, not a recommendation for physical
printing, OCR use, or bundling. Redistribution remains blocked on a
file-by-file license and embedding review.

### OCR-B alignment and character spacing

The constrained 6 pt OCR-B grid used 150 randomized rows, 300 DPI, and 100
calculated lines per page. Left alignment with 0 pt added spacing retained all
16 symbols with no erasures and yielded 5,050 usable bytes/page. Adding 0.1 pt
reduced row width by one character and capacity to 5,000 bytes/page without an
accuracy benefit. Centering at 0 pt lost `A` and `4`, fell to radix 8, and
yielded 3,787.5 bytes/page; 0.1 pt spacing recovered all symbols but still
yielded only 5,000 bytes/page. Justification at 0 pt tied the left-aligned
5,050-byte result rather than improving it, while 0.1 and 0.2 pt introduced
one and two erased rows respectively. Larger spacing reduced characters per
row in every alignment.

The best measured grid is therefore left aligned with no added character
spacing. Justification did not create useful OCR separation and sometimes
worsened erasures. These values are constrained character-grid diagnostics,
not CI performance evidence or proof of full-frame recovery.

The matching constrained Courier 8 pt matrix also favored zero added spacing:
left and centered placement each retained 16/16 symbols with no erasures at
4,125 usable bytes/page. Justification at 0 pt fell to 14 safe symbols and
radix 8; 0.1 pt recovered radix 16 but yielded only 4,023 bytes/page. Thus
neither centering, justification, nor added tracking displaced OCR-B left/0 as
the densest measured constrained grid.

The constrained profile subsequently passed a complete VM wire gate: a
12,036-byte source was archived with Zstandard into five PDF pages using the
bundled OCR-B font at 6 pt and minimum margins, rasterized at 300 DPI,
recognized with pinned `tessdata_fast`, and restored byte-for-byte. Tesseract
removed frame-separator spaces, so all five display page hashes warned; compact
CRC-checked frame parsing and payload FEC still recovered the exact archive.
This is a synthetic PDF/raster gate, not evidence for a physical printer and
scanner.

## Tesseract model pins

Model files are downloaded for reproducible evaluation and are not bundled in
the Python package.

| Model repository | Pin | `eng.traineddata` SHA-256 |
| --- | --- | --- |
| [`tesseract-ocr/tessdata`](https://github.com/tesseract-ocr/tessdata/tree/ced78752ad740321a8d6159fc0c3e21b05a5a912) | `ced78752ad740321a8d6159fc0c3e21b05a5a912` | `daa0c97d651b3c30b23736f4c213c7832bfdda6f2e4440f5c8d42e85346dc709` |
| [`tesseract-ocr/tessdata_best`](https://github.com/tesseract-ocr/tessdata_best/tree/e12c65a) | `e12c65a` | `8280aed0782fe2722476feb6d45aaf7b7e3d4c5939592ef719b5982fe08dabe6` |
| [`tesseract-ocr/tessdata_fast`](https://github.com/tesseract-ocr/tessdata_fast/tree/8741641) | `8741641` | `7d4322bd2a774972c327e7b5ba0c3bf163128d906c7b7b03571f7fb1009e71a3` |

Before custom training, follow Tesseract's official
[quality guidance](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html):
test page segmentation, a channel-specific whitelist, dictionary settings,
resolution, and the available official models. Any later fine-tuning must use
held-out Glyphive frame lines and exact channel characters rather than scoring
on its synthetic training samples.

## Pending matrix

- Repeat the constrained OCR-B restore gate through physical print/scan paths,
  and run the same full-frame comparison for Courier.
- Continue OCR-B point-size, rendered stroke-width, line-spacing, and
  minimum-margin tests. The first alignment sweep favored left alignment with
  no added spacing; repeat it through an end-to-end restore path before making
  that diagnostic winner a wire profile.
- Source separately identifiable OCR-B constant-stroke and letterpress outlines;
  do not infer them from a filename or synthetic bolding.
- Repeat the Tsukurimashou regular and Sharp candidates through physical
  print/scan paths; do not promote or bundle them without resolving the Sharp
  design warning and file-specific redistribution terms.
