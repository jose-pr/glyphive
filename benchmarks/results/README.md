# Benchmark history

Machine-readable timing and OCR results, one JSON file per measured cell or
environment. These files support regression investigation; a checked-in result
does not by itself establish a public performance claim.

## Producing a result

From an editable install of Glyphive:

```console
python benchmarks/run.py --save
python benchmarks/run.py --save --name glyphive-candidate-py314
```

Run without `--save` for the human table only. The default filename stem is
`glyphive-<version>-py<major><minor>`; `--name <x>` overrides it. Prefer results
from the benchmark CI job. Ad hoc workstation or VM runs are sanity checks, not
release-performance evidence.

## Workloads

The suite uses deterministic in-memory inputs so it measures core format work,
not fixture loading or machine-specific storage:

- `codec.g1.encode_1k` / `codec.g1.decode_1k`: `g1` framing, CRC, and
  Reed-Solomon work on a fixed 1 KiB payload.
- `codec.g1.encode_16k` / `codec.g1.decode_16k`: the same path on a fixed 16 KiB
  payload, exposing size-dependent behavior across many RS blocks and lines.
- `layout.paginate_16k`: page metadata, footer hashing, and pagination over the
  precomputed 16 KiB encoded lines. Codec time is intentionally excluded.

Compression, archive traversal, rendering, filesystem I/O, and OCR are excluded:
they either belong to separate subsystem benchmarks or introduce external noise.
Payload digests and all codec/layout parameters are recorded in each result.

## Schema

Each JSON object contains:

- `schema_version`, `name`, `glyphive_version`, and UTC `timestamp`;
- Python implementation/version, platform, processor, dependency versions, and
  Git commit/dirty state;
- `iterations`: one warmup count, repeat count, and fixed inner count per metric;
- `workloads`: payload sizes/digests plus codec and layout parameters;
- `metrics`: `min_ms`, `median_ms`, and `max_ms` per call for every workload.

Compare `median_ms`; min/max make timing noise visible. Only compare files from
the same benchmark schema, machine class, interpreter, dependency set, workload
parameters, and inner/repeat counts. A dirty Git result is useful for development
but should not be treated as a release baseline.

## Files

- `glyphive-<version>-py<ver>.json` — conventional release/interpreter result.
- Custom names may identify a commit, CI runner, or controlled experiment.
- `ocr-*.json` records raw per-character OCR observations and derived capacity.
- [PROVENANCE.md](PROVENANCE.md) identifies the shared environment and limits
  of the currently checked-in evidence.
