"""Tests for findasbuilts.scan.iter_documents.

Covers the extension filter, case-insensitive suffixes, absolute-path
sorting, dot-directory skipping, and the nonexistent-root case (which
yields nothing -- the cli's explicit missing-directory exit-2 check is
covered in test_cli.py).
"""

import os

from findasbuilts.scan import iter_documents


def _names(root, extensions):
    return [os.path.basename(p) for p in iter_documents(str(root), extensions)]


class TestIterDocuments:
    def test_extension_filter(self, tmp_path):
        (tmp_path / "a.pdf").write_text("x", encoding="utf-8")
        (tmp_path / "b.tif").write_text("x", encoding="utf-8")
        (tmp_path / "c.txt").write_text("x", encoding="utf-8")
        assert _names(tmp_path, [".pdf", ".tif"]) == ["a.pdf", "b.tif"]

    def test_case_insensitive_suffixes(self, tmp_path):
        (tmp_path / "a.PDF").write_text("x", encoding="utf-8")
        (tmp_path / "b.Tif").write_text("x", encoding="utf-8")
        assert _names(tmp_path, [".pdf", ".tif"]) == ["a.PDF", "b.Tif"]

    def test_sorted_by_absolute_path(self, tmp_path):
        for name in ["z.pdf", "a.pdf", "m.pdf"]:
            (tmp_path / name).write_text("x", encoding="utf-8")
        assert _names(tmp_path, [".pdf"]) == ["a.pdf", "m.pdf", "z.pdf"]

    def test_skips_dot_directories(self, tmp_path):
        (tmp_path / "keep.pdf").write_text("x", encoding="utf-8")
        hidden = tmp_path / ".git"
        hidden.mkdir()
        (hidden / "skip.pdf").write_text("x", encoding="utf-8")
        assert _names(tmp_path, [".pdf"]) == ["keep.pdf"]

    def test_recurses_into_subdirectories(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.pdf").write_text("x", encoding="utf-8")
        assert _names(tmp_path, [".pdf"]) == ["nested.pdf"]

    def test_nonexistent_root_yields_nothing(self, tmp_path):
        assert _names(tmp_path / "does-not-exist", [".pdf"]) == []

    def test_empty_directory_yields_nothing(self, tmp_path):
        assert _names(tmp_path, [".pdf"]) == []
