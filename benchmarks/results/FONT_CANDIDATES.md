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
| Tsukurimashou OCR fonts | [Tsukurimashou project](https://tsukurimashou.org/) and its [OCR font design notes](https://tsukurimashou.org/ocr.pdf) | Licensing and embedding terms require a separate review; not bundled | Pending controlled measurement |

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
- Measure Tsukurimashou candidates after recording exact files, hashes, and
  redistribution terms.
