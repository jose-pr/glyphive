# Contributing to Glyphive

Thanks for helping improve Glyphive. The project is alpha: wire-format and CLI
changes are welcome when they include tests and clear documentation.

## Development setup

```bash
git clone https://github.com/jose-pr/glyphive.git
cd glyphive
python -m venv .venv/dev
python -m pip install -e ".[all,dev,docs]"
```

Activate the environment using the command appropriate for your shell, or call
its Python executable directly.

## Checks

Run the automated suite and strict documentation build before opening a pull
request:

```bash
python -m pytest -q
python -m mkdocs build --strict
```

Core timing checks are separate from correctness tests:

```bash
python benchmarks/run.py
```

Local timings are sanity checks, not publishable performance evidence. Keep
performance claims tied to comparable CI results. OCR sweeps and other
compute-heavy experiments should run on an appropriate remote machine and must
record engine, model, font, size, DPI, platform, and corpus provenance.

## Changes and commits

- Add focused tests for behavior changes.
- Update the relevant guide and `CHANGELOG.md` for user-visible changes.
- Do not commit generated `site/`, `dist/`, temporary OCR output, or agent files.
- Use `type: description` commits, such as `feat:`, `fix:`, `docs:`, `test:`,
  or `chore:`.
- Keep unrelated changes in separate commits.

## Pull requests and bug reports

Open a focused pull request describing the behavior, its tests, and any wire or
density impact. Bug reports should include the Glyphive and Python versions, the
operating system, the exact command, expected and observed behavior, and a small
reproducer when possible. Remove private archive contents from transcripts and
images before attaching them publicly.
