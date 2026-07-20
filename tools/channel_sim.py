"""Simulate an OCR substitution channel against a codec's full decode path.

Applies i.i.d. character substitutions (each printed alphabet character is
replaced by a different random alphabet character with probability ``--cer``)
to a freshly encoded document, then attempts a full decode. Substitution is
the dominant OCR failure mode measured for this project (insertions and
line drops are separately defended by index masking and ``split_frame``);
this harness answers "at what character error rate does the *format*
stop recovering?", independent of any particular OCR engine.

The optional ``--repair`` tier prototypes CRC-guided single-substitution
repair (see ``.agents/plans/codec_review/01_decode_hardening.md``): for each
CRC-failed line, every single-character substitution over the printed
index+payload is tested against the printed check field, plus the
"check field itself is the corrupted character" case; a repair is accepted
only when exactly ONE candidate matches. Acceptance therefore remains
CRC-oracle-driven -- this is measurement tooling for the planned decode
hardening, not a bypass of the codec's no-guessing discipline.

Examples::

    python tools/channel_sim.py                     # default sweep, base16g
    python tools/channel_sim.py --cer 0.002 --docs 20
    python tools/channel_sim.py --repair --codec base32g-crc16-rs

Per (cer, mode) cell the report shows: decode successes, mean CRC-failed
lines per doc, mean lines whose *claimed index* exceeded the true stream
extent (the geometry-poisoning signal), and the exception type that ended
each failed decode.
"""

from __future__ import annotations

import argparse
import collections
import random
import sys
import typing as _ty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from glyphive.codec import get as get_codec  # noqa: E402
from glyphive.codec.base16c import (  # noqa: E402
    _RadixSpec,
    _check_chars,
    _parse_line,
)


def corrupt_line(rng: random.Random, line: str, cer: float, spec: _RadixSpec) -> str:
    alphabet = spec.alphabet
    out = []
    for ch in line:
        if ch in alphabet and rng.random() < cer:
            out.append(rng.choice([c for c in alphabet if c != ch]))
        else:
            out.append(ch)
    return "".join(out)


def crc_guided_repair(line: str, spec: _RadixSpec) -> _ty.Optional[str]:
    """Return the repaired line, or ``None`` (no hit / ambiguous / unframed)."""
    parts = line.split()
    if len(parts) != 3 or not parts[2].startswith(spec.delimiter):
        return None
    label, payload, check = parts
    kind, token, want = label[:1], label[1:], check[1:]
    if len(token) != spec.index_width or len(want) != spec.check_width:
        return None
    body = token + payload
    hits: _ty.List[str] = []
    for pos in range(len(body)):
        for sub in spec.alphabet:
            if sub == body[pos]:
                continue
            cand = body[:pos] + sub + body[pos + 1:]
            if _check_chars(cand[: len(token)], cand[len(token):], spec) == want:
                hits.append(cand)
                if len(hits) > 1:
                    return None
    expected = _check_chars(token, payload, spec)
    if expected != want:
        # The corruption may be inside the printed check field itself: accept
        # iff the recomputed check is within Hamming distance 1 of the printed
        # one, and emit the RECOMPUTED check so the line re-parses as valid.
        if sum(a != b for a, b in zip(expected, want)) == 1:
            hits.append(body)
            if len(hits) > 1:
                return None
    if len(hits) != 1:
        return None
    cand = hits[0]
    token2, payload2 = cand[: len(token)], cand[len(token):]
    check2 = _check_chars(token2, payload2, spec)
    return f"{kind}{token2} {payload2} {spec.delimiter}{check2}"


def run_cell(
    codec_name: str,
    cer: float,
    docs: int,
    nbytes: int,
    seed: int,
    repair: bool,
) -> _ty.Dict[str, _ty.Any]:
    codec = get_codec(codec_name)
    spec = codec._spec
    ok = 0
    bad_lines = geometry_poisoned = 0
    failures: _ty.Counter[str] = collections.Counter()
    for doc in range(docs):
        rng = random.Random(seed + doc)
        data = rng.randbytes(nbytes)
        lines = codec.encode(data)
        true_max = {
            kind: sum(1 for l in lines if l.startswith(kind)) - 1
            for kind in ("L", "P")
        }
        noisy = [corrupt_line(rng, l, cer, spec) for l in lines]
        feed = []
        for line in noisy:
            parsed = _parse_line(line, spec)
            if parsed is not None and not parsed.ok:
                bad_lines += 1
                if parsed.idx > true_max.get(parsed.kind, -1):
                    geometry_poisoned += 1
                if repair:
                    fixed = crc_guided_repair(line, spec)
                    if fixed is not None:
                        feed.append(fixed)
                        continue
            feed.append(line)
        try:
            ok += codec.decode(feed) == data
        except Exception as exc:  # noqa: BLE001 -- report, never mask
            failures[type(exc).__name__] += 1
    return {
        "ok": ok,
        "docs": docs,
        "bad_lines": bad_lines / docs,
        "geometry_poisoned": geometry_poisoned / docs,
        "failures": dict(failures),
    }


def main(argv: _ty.Optional[_ty.Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--codec", default="base16g-crc16-rs")
    parser.add_argument(
        "--cer",
        type=float,
        nargs="+",
        default=[0.0005, 0.001, 0.002, 0.005, 0.01],
        help="character substitution rate(s) to sweep",
    )
    parser.add_argument("--docs", type=int, default=10, help="documents per cell")
    parser.add_argument("--bytes", type=int, default=30000, dest="nbytes")
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument(
        "--repair",
        action="store_true",
        help="apply the prototype CRC-guided single-substitution repair tier",
    )
    args = parser.parse_args(argv)

    print(
        f"codec={args.codec} docs/cell={args.docs} doc={args.nbytes} B "
        f"repair={'on' if args.repair else 'off'}"
    )
    header = f"{'CER':>8} | {'ok':>5} | {'bad lines/doc':>13} | {'geom-poison/doc':>15} | failures"
    print(header)
    print("-" * len(header))
    for cer in args.cer:
        cell = run_cell(
            args.codec, cer, args.docs, args.nbytes, args.seed, args.repair
        )
        fail_text = (
            ", ".join(f"{k}x{v}" for k, v in sorted(cell["failures"].items()))
            or "-"
        )
        print(
            f"{cer:8.4f} | {cell['ok']:2d}/{cell['docs']:2d} | "
            f"{cell['bad_lines']:13.1f} | {cell['geometry_poisoned']:15.1f} | "
            f"{fail_text}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
