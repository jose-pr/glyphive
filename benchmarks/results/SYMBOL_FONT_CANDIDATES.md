# Symbol and CJK font candidate provenance (draft)

This is a read-only inventory of external Tsukurimashou project artifacts. No
font is bundled or recommended, and no OCR accuracy or density claim is made.
Artifacts were downloaded and inspected on the Rocky Linux VM on 2026-07-16.
SHA-256 covers the exact downloaded ZIP or extracted font file. Coverage is the
union of Unicode `cmap` entries reported by FontTools 4.60.2; it does not prove
that two code points have distinct outlines or survive OCR.

## Downloaded packages

| Package | Source | ZIP SHA-256 |
| --- | --- | --- |
| Genjimon 0.2 | `https://tsukurimashou.org/files/genjimon-0.2.zip` | `c134aba583c139b9bb50bc5936344a23ae9eaf4fa0f8d453e778047fc2c625f1` |
| Tsukurimashou OTF 0.11 | `https://tsukurimashou.org/files/tsukurimashou-otf-0.11.zip` | `a01fa361fd48f4ac55f2040a40a825b8432bac050a7db33130eb9466cebad89d` |
| TsuIta OTF 0.11 | `https://tsukurimashou.org/files/tsuita-otf-0.11.zip` | `5aa864d8a6b4934b0733bd458f8d44ecfcb655108dbdfbb8d473080e1ec587e4` |
| Mandeubsida OTF 0.11 | `https://tsukurimashou.org/files/mandeubsida-otf-0.11.zip` | `a48570eb9e2469554a3a73c2a80c3194aba4385adc2880f17b61faec8ea76352` |
| Tsukurimashou source 0.11 | `https://tsukurimashou.org/files/tsukurimashou-0.11.zip` | `2e305f09662a86d792e89da1bbdaa3c254d470ec0bc6cd69e887f60731845ec0` |

## Licensing boundary

The source package's `COPYING` says the family and generated fonts are
generally GPL-3.0 with a font-embedding exception, while individual files may
carry other terms. The font-only OTF files embed the same GPL-3.0 plus
font-embedding clarification in name ID 13 and explicitly warn that source-code
requirements generally prevent redistribution on ordinary free-font sites.
The standalone Genjimon ZIP contains GPL-3.0 but no matching embedding exception
in its `COPYING`. These artifacts are therefore evaluation inputs only. A
file-by-file legal review and corresponding source offer would be required
before redistribution or bundling.

## Unicode coverage groups

All fonts in a row have identical measured coverage counts. “Symbols” means
Unicode general categories `S*`; “punctuation” means `P*`.

| Families/styles | Fonts | Unicode | Printable ASCII | CJK unified | Hiragana | Katakana | Hangul syllables | Symbols | Punctuation | Private use |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tsukurimashou Kaku, Mincho, Anbiruteki, Bokukko, Maru, Tenshi no Kami; TsuIta Atama/Soku; ordinary and PS variants | 16 | 4,139 | 95/95 | 2,739 | 93 | 95 | 0 | 487 | 99 | 91 |
| Mandeubsida Batang, Dodum, Sun-Moon; ordinary and PS variants | 6 | 12,824 | 95/95 | 0 | 93 | 95 | 11,172 | 314 | 99 | 91 |
| Tsukurimashou OCR A | 2 | 115 | 95/95 | 0 | 0 | 0 | 0 | 17 | 27 | 0 |
| Tsukurimashou OCR B/B E/B F/B L/B S/B X | 12 | 140 | 95/95 | 0 | 0 | 0 | 0 | 20 | 30 | 0 |
| Genjimon standalone/source-package builds | 12 | 56 | 55/95 | 0 | 0 | 0 | 0 | 0 | 2 | 0 |

The Japanese group also covers 4 CJK Extension A and 1 CJK Compatibility
Ideograph in this inventory. Genjimon does not encode the I Ching hexagrams at
their Unicode symbol code points: its cmap is space, `A`-`Z`, `[`, `a`-`z`,
`{`, and no-break space. It is a specialized ASCII-mapped display font, not a
large Unicode symbol alphabet.

## Extracted font hashes

Names and styles below come from OpenType name IDs 1 and 2. Every listed style
is `Regular` except standalone Genjimon, whose style names are in the Style
column.

### Genjimon 0.2

| File | Family | Style | SHA-256 |
| --- | --- | --- | --- |
| `GenjimonBlack.ttf` | Genjimon | Black | `a8fe5a2186c6a63ca39c9d61b5118d5a06bd167a44bbf96c3f1c5b7914772d6f` |
| `GenjimonMedium.ttf` | Genjimon | Medium | `fa4ed43baa69265b19092b5ac3a4060a0425550a543dcdaa4f1128495d2374f6` |
| `GenjimonReverse.ttf` | Genjimon | Reverse | `197a3e004935822ecbd8cfbbc00de52345bea8597336c9ef000b9778760f931f` |
| `GenjimonRound.ttf` | Genjimon | Round | `a47989dddc11e1e220238d3bbc9a6967b5fe203b4f443b9b733816b9e4794417` |
| `GenjimonRoundOutline.ttf` | Genjimon | RoundOutline | `e7c7e3be6f1f247eefed5d2b9042293b351a0c3f314332d834b42aa0e2095ba6` |
| `GenjimonWhite.ttf` | Genjimon | White | `14a2240425422adda854e96d154d59d29f2f831e938c50f717cbd9bd72b6c987` |

### Tsukurimashou OTF 0.11

| File/family | SHA-256 | PS file/family | PS SHA-256 |
| --- | --- | --- | --- |
| `TsukurimashouAnbiruteki.otf` / Tsukurimashou Anbiruteki | `610e40ccbe1f54a9b358f79f2281ac03c21e3202fd15f8ad58cc9def2854c51b` | `TsukurimashouAnbirutekiPS.otf` / Tsukurimashou Anbiruteki PS | `cd785f673426c12d1c0f33b1494c9c30578e7754820302994baa090b0275e977` |
| `TsukurimashouBokukko.otf` / Tsukurimashou Bokukko | `79c18e7a787e291bc1e9e586689c07c910c69ce573b53964522496d1042aa06e` | `TsukurimashouBokukkoPS.otf` / Tsukurimashou Bokukko PS | `1f08e71e3e3af3c377302eae111e69db4e9f6049ad706124dfff8e168ab85c2b` |
| `TsukurimashouKaku.otf` / Tsukurimashou Kaku | `8063e958d34c1991ac772a00cbbabe827bfe2536c644203426c7dba1bbb43f7d` | `TsukurimashouKakuPS.otf` / Tsukurimashou Kaku PS | `2339ca274f33540c1d51891e8607bf254054cbb10e463bb5327f1c66e80c9fa6` |
| `TsukurimashouMaru.otf` / Tsukurimashou Maru | `65f95ebf63f9384c297c545689312ce2526abf777f1aa31f9160020c908b581a` | `TsukurimashouMaruPS.otf` / Tsukurimashou Maru PS | `268f15878ec0db7c52ae6da5a4710e71219b882c124b40e9de5ee582fa2a83cb` |
| `TsukurimashouMincho.otf` / Tsukurimashou Mincho | `2f8207ec5bd8840c6dfe79def05d4c59ea8fa66968db9fece0d108b77d0f05ce` | `TsukurimashouMinchoPS.otf` / Tsukurimashou Mincho PS | `3517d5ec03e927111926ce4ba73a3eb45f2a87bfd021d0efab97c4eb3cce7359` |
| `TsukurimashouTenshinoKami.otf` / Tsukurimashou Tenshi no Kami | `546716264cb79ff86c0f2f2d56f17f902c547fb750503a31731dbd0dfd54f82b` | `TsukurimashouTenshinoKamiPS.otf` / Tsukurimashou Tenshi no Kami PS | `c1257bbacae5ee6f274fcd6751ef5e0c1386370ddfaf8a1ecade2c694f01e9f3` |

### TsuIta and Mandeubsida OTF 0.11

| File/family | SHA-256 |
| --- | --- |
| `TsuItaAtama.otf` / TsuIta Atama | `a97c1fcb8edd5b70f093889c77154614b5ed0193120a8fc1506ff9ab9cffcac2` |
| `TsuItaAtamaPS.otf` / TsuIta Atama PS | `19dd10bb54d6a32a2d281e2a4b8a5f7d0c29b91160459ebdeff0b2f261104b11` |
| `TsuItaSoku.otf` / TsuIta Soku | `cf97a443710698f0ccd9b287d6f91fbc093a453261305e0dd051b657bd104572` |
| `TsuItaSokuPS.otf` / TsuIta Soku PS | `72a3d45a6c5f710afd9a7d3f847cad86636f31cb7f94bdd0aa5781b5537bae9b` |
| `MandeubsidaBatang.otf` / Mandeubsida Batang | `21c33a7fb5abf251aeafd5b9fbb9af92c022e51067b67a6a47944726638700ce` |
| `MandeubsidaBatangPS.otf` / Mandeubsida Batang PS | `377fc7fd10842bfddadf1e42fc5c49bf9766a1c0eea39116a6da2e39f447492a` |
| `MandeubsidaDodum.otf` / Mandeubsida Dodum | `e51adce3d3eac8961b08c1ff48281c43efd9d23799cf567ab716eb7964b485e9` |
| `MandeubsidaDodumPS.otf` / Mandeubsida Dodum PS | `6408d2b0d7c248d70f6af00476d6269c9a9d4726f08fa03de185af81e651cbd4` |
| `MandeubsidaSunMoon.otf` / Mandeubsida Sun-Moon | `b7c398e8efbb384ccda9ad9b328b6146c8650c80eeb2063a8bcd34dd9f2f167c` |
| `MandeubsidaSunMoonPS.otf` / Mandeubsida Sun-Moon PS | `0d22f6602f554856e900019a3ba879efbf7c5c81faab15cc2d137c109a549f53` |

### Additional fonts in Tsukurimashou source 0.11

The source ZIP's four Kaku/Mincho OTF hashes are identical to the corresponding
font-only ZIP rows above. Its Genjimon builds use family names such as
`Genjimon Black` with style `Regular` and have these distinct hashes:

| File | SHA-256 |
| --- | --- |
| `GenjimonBlack.ttf` | `f8fd6e085e04cd2ee811814865014a302ecd8306d941616cd514177e3d96ea04` |
| `GenjimonMedium.ttf` | `c69e93dc298ed90ee8178a290488e4d6b7acf5a38ecd66812a065ce493c552e2` |
| `GenjimonReverse.ttf` | `5cc51e23951fdf87b188ee55844ebebe3efd1ce45ae284877bc6631c93c0bd43` |
| `GenjimonRound.ttf` | `96d6ec3312810e3b905abd292d0917857538f463d027190d70206fbd386fecb0` |
| `GenjimonRoundOutline.ttf` | `32c95ae0830ae3666f0689a0694f3ebc3a44fe68f033db0e12e7f6bed5844e6e` |
| `GenjimonWhite.ttf` | `5a54223f0048878a28a897de6df4847e6ca49e9c06e5fe7344f70c7e9518b4b1` |

The source ZIP also contains regular-style OCR fonts:

| File/family | SHA-256 | File/family | SHA-256 |
| --- | --- | --- | --- |
| `OCRA.otf` / OCR A | `cc92c222ac2bf30a9eaf9000e764eb4ceafdd6456acfc0ade2c1215e8c89bef6` | `OCRA.ttf` / OCR A | `7eaba319ee85acc7dc41674a2c2f2484226e028ff05442a2c5bc82d5c57b36de` |
| `OCRB.otf` / OCR B | `ee931ba3c7ffb94ca918607b023699fa84b7512b70235177792ed0376244a14d` | `OCRB.ttf` / OCR B | `886e01778e31667804066251063a4a1b958404898ebecc29278565e49a3f6e0a` |
| `OCRBE.otf` / OCR B E | `8b4189b70c4eae7084fe5a9116b9f984d86ef967a6b191c81008b01e3ee23a5e` | `OCRBE.ttf` / OCR B E | `172bd6c70800ac8f44d1367e2894cb145d7577bd78594fa74e2d67813080ba09` |
| `OCRBF.otf` / OCR B F | `2f0eadbb3bef8a2a991f0e8468d3f764137665432d4efd7f254bfe551aa3698d` | `OCRBF.ttf` / OCR B F | `15993c70c4e9f0457ffd815487097ae95bc4fdaffcb47c8276c6a6d65ccf65ce` |
| `OCRBL.otf` / OCR B L | `becfa3d79678987cef8292657d0b732dc77a0398731927edf499bec595aa41da` | `OCRBL.ttf` / OCR B L | `5912b7122dd9ac1f1c7ae6ad99f5f2a4c84110785b8b8f9aa50ce7ab14acd7c9` |
| `OCRBS.otf` / OCR B S | `ec7a858fb8ebeed8b2600a10090ff5420d7115b7567dac595dc8b97ec60acbff` | `OCRBS.ttf` / OCR B S | `0d7a378e6ddc29557b6022cf3e7cb24ea716e44cf1e684868de5e7baf6432a4e` |
| `OCRBX.otf` / OCR B X | `e0d870788b2e412a0c574e56358d45a82bb351033d2152b213bda67f9787b17f` | `OCRBX.ttf` / OCR B X | `d881b5549e9e4ce6b5c0e669cfcc25289664ecaa0281f23aadb93ce6b0b0cdfd` |

## Candidate ranking for future channel work

1. **Japanese Kanji/symbol trials:** start with Tsukurimashou Kaku and Mincho
   as the source-package baseline, then compare Maru and the high-contrast
   Anbiruteki/Tenshi no Kami styles. All share the same 4,139-code-point cmap;
   only measured OCR can distinguish their usable subsets.
2. **Korean/Hangul trials:** Mandeubsida Dodum and Batang expose the broadest
   inventory here (12,824 code points and all 11,172 modern Hangul syllables).
3. **Specialized binary glyph trial:** Genjimon styles may be interesting as
   deliberately distinct artwork, but their ASCII-mapped 56-code-point cmap is
   not a Unicode symbol/Kanji channel and requires a custom semantic mapping.

No candidate should enter a wire preset until randomized held-out glyph sweeps,
line insertion/erasure measurements, and a complete byte-for-byte restore gate
are recorded for a pinned font, renderer, DPI, and OCR model.
