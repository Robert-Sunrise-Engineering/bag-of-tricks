"""Tests for findasbuilts.verdict.evaluate_document.

Covers the STAMP / NO_STAMP decision and that all fields are carried
through. ERROR verdicts are constructed by cli.py (not evaluate_document),
so they're exercised in test_cli_main.py.
"""

from findasbuilts.keywords import StampCluster
from findasbuilts.verdict import evaluate_document


def _stamp(designation="p.e.", license_number="No. 12345", page=2, distance=1, snippet="P.E. No. 12345"):
    return StampCluster(
        designation=designation,
        license_number=license_number,
        page=page,
        distance=distance,
        snippet=snippet,
    )


class TestEvaluateDocument:
    def test_cluster_yields_stamp(self):
        stamp = _stamp()
        v = evaluate_document(
            "C:/docs/a.pdf", stamp, page_count=3, text_source="text_layer"
        )
        assert v.path == "C:/docs/a.pdf"
        assert v.has_stamp is True
        assert v.verdict == "STAMP"
        assert v.stamp_keywords == ["p.e."]
        assert v.stamp_pages == {"p.e.": [2]}
        assert v.evidence == "P.E. No. 12345"
        assert v.page_count == 3
        assert v.text_source == "text_layer"
        assert v.error == ""

    def test_no_cluster_yields_no_stamp(self):
        v = evaluate_document(
            "C:/docs/b.pdf", None, page_count=1, text_source="ocr"
        )
        assert v.has_stamp is False
        assert v.verdict == "NO_STAMP"
        assert v.stamp_keywords == []
        assert v.stamp_pages == {}
        assert v.evidence == ""
        assert v.error == ""

    def test_fields_carried_through(self):
        stamp = _stamp(designation="professional engineer", page=1, snippet="...")
        v = evaluate_document(
            "C:/docs/c.tif", stamp, page_count=2, text_source="image_ocr"
        )
        assert v.verdict == "STAMP"
        assert v.stamp_keywords == ["professional engineer"]
        assert v.stamp_pages == {"professional engineer": [1]}
        assert v.evidence == "..."
        assert v.page_count == 2
        assert v.text_source == "image_ocr"
