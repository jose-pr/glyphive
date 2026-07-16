"""Tests for the ignore filter in :func:`glyphive.archive.list_paths`.

Root-level ``.gitignore`` / ``.ignore`` are honored by default, ``use_ignore=False``
disables them, and ``extra_ignore`` excludes additional patterns.
"""

import pytest

from glyphive import archive


def _make_tree(root, ignore_filename):
    (root / ignore_filename).write_text("*.log\n", encoding="utf-8")
    (root / "keep.txt").write_text("keep me\n", encoding="utf-8")
    (root / "drop.log").write_text("drop me\n", encoding="utf-8")


@pytest.mark.parametrize("ignore_filename", [".gitignore", ".ignore"])
def test_ignore_excludes_by_default(tmp_path, ignore_filename):
    _make_tree(tmp_path, ignore_filename)

    default = archive.list_paths(tmp_path)
    assert "keep.txt" in default
    assert "drop.log" not in default


@pytest.mark.parametrize("ignore_filename", [".gitignore", ".ignore"])
def test_no_ignore_includes_everything(tmp_path, ignore_filename):
    _make_tree(tmp_path, ignore_filename)

    everything = archive.list_paths(tmp_path, use_ignore=False)
    assert "keep.txt" in everything
    assert "drop.log" in everything


def test_extra_ignore_excludes(tmp_path):
    # No ignore file present; exclusion comes solely from extra_ignore.
    (tmp_path / "keep.txt").write_text("keep\n", encoding="utf-8")
    (tmp_path / "secret.key").write_text("secret\n", encoding="utf-8")

    filtered = archive.list_paths(tmp_path, extra_ignore=["*.key"])
    assert "keep.txt" in filtered
    assert "secret.key" not in filtered

    # Without the extra pattern, both appear.
    unfiltered = archive.list_paths(tmp_path)
    assert "secret.key" in unfiltered
