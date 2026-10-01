"""Tests for findasbuilts.stamp.detect_stamp.

detect_stamp is a thin semantic wrapper over keywords.match_stamp_cluster;
the matching rules themselves are covered exhaustively in test_keywords.py.
These tests pin the delegation contract: a cluster yields a StampCluster,
and no cluster yields None.
"""

from findasbuilts.stamp import detect_stamp


class TestDetectStamp:
    def test_delegates_to_match_stamp_cluster(self):
        text_by_page = {1: "P.E. No. 123456"}
        result = detect_stamp(text_by_page, ["p.e."])
        assert result is not None
        assert result.designation == "p.e."
        assert result.license_number == "No. 123456"
        assert result.page == 1

    def test_no_cluster_returns_none(self):
        result = detect_stamp({1: "plain prose"}, ["p.e."])
        assert result is None

    def test_designation_alone_returns_none(self):
        # A signature block's "P.E." with no license number nearby is not a
        # stamp -- the cluster rule is what separates a stamp from a title.
        result = detect_stamp({1: "Jesse Ralphs, P.E."}, ["p.e."])
        assert result is None

    def test_license_alone_returns_none(self):
        result = detect_stamp({1: "License No. 123456"}, ["p.e."])
        assert result is None
