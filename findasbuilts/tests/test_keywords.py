"""Tests for findasbuilts.keywords -- the correctness core.

Covers match_keywords (case-insensitivity, whole-word boundaries, plurals,
``p.e.`` at end-of-string and before punctuation, multi-word keywords, the
intended overlap between ``professional engineer`` and ``registered
professional engineer``, page tracking, dedup, and empty/no-match cases) and
match_stamp_cluster (the proximity rule: a designation within max_distance
characters of a license number on the same page). stamp.py's detect_stamp
(the thin semantic wrapper) is covered in test_stamp.py.
"""

import re

import findasbuilts.keywords as keywords
from findasbuilts.keywords import match_keywords, match_stamp_cluster


def _match(text, keywords):
    return match_keywords({1: text}, keywords)


def _cluster(text, designations, max_distance=250):
    return match_stamp_cluster({1: text}, designations, max_distance=max_distance)


class TestCaseInsensitivity:
    def test_uppercase_text_matches_lowercase_keyword(self):
        result = _match("LICENSE NO 12345", ["license no"])
        assert result.matched == ["license no"]
        assert result.count == 1

    def test_mixed_case_keyword_matches(self):
        result = _match("Registered Professional Engineer", ["professional engineer"])
        assert result.matched == ["professional engineer"]


class TestWordBoundaries:
    def test_keyword_not_matched_inside_longer_word(self):
        # "valve" must not match inside "valvebox".
        result = _match("valvebox assembly", ["valve"])
        assert result.matched == []

    def test_keyword_matched_as_standalone_word(self):
        result = _match("the valve is closed", ["valve"])
        assert result.matched == ["valve"]

    def test_keyword_not_matched_when_followed_by_alphanumeric(self):
        result = _match("valve2", ["valve"])
        assert result.matched == []

    def test_keyword_not_matched_when_preceded_by_alphanumeric(self):
        result = _match("xvalve", ["valve"])
        assert result.matched == []


class TestPlurals:
    def test_plural_text_matches_singular_keyword(self):
        result = _match("engineer seals", ["engineer seal"])
        assert result.matched == ["engineer seal"]

    def test_singular_text_matches_singular_keyword(self):
        result = _match("engineer seal", ["engineer seal"])
        assert result.matched == ["engineer seal"]


class TestPePunctuation:
    def test_pe_at_end_of_string(self):
        result = _match("P.E.", ["p.e."])
        assert result.matched == ["p.e."]

    def test_pe_before_newline(self):
        result = _match("P.E.\n", ["p.e."])
        assert result.matched == ["p.e."]

    def test_pe_before_comma(self):
        result = _match("P.E.,", ["p.e."])
        assert result.matched == ["p.e."]

    def test_pe_before_period(self):
        result = _match("P.E..", ["p.e."])
        assert result.matched == ["p.e."]

    def test_pe_before_space(self):
        result = _match("P.E. 12345", ["p.e."])
        assert result.matched == ["p.e."]

    def test_pe_not_matched_when_followed_by_alphanumeric(self):
        result = _match("P.E.123", ["p.e."])
        assert result.matched == []


class TestMultiWordKeywords:
    def test_multi_word_keyword_matches(self):
        result = _match("certificate of authorization", ["certificate of authorization"])
        assert result.matched == ["certificate of authorization"]

    def test_multi_word_keyword_with_extra_whitespace_does_not_match(self):
        # The regex matches the literal keyword; doubled spaces break it.
        result = _match("certificate  of  authorization", ["certificate of authorization"])
        assert result.matched == []


class TestOverlap:
    def test_overlapping_keywords_both_match(self):
        # "registered professional engineer" contains "professional engineer";
        # both are evidence and both must be reported.
        result = _match(
            "registered professional engineer",
            ["professional engineer", "registered professional engineer"],
        )
        assert result.matched == ["professional engineer", "registered professional engineer"]
        assert result.count == 2


class TestPageTracking:
    def test_pages_recorded_per_keyword(self):
        text_by_page = {
            1: "cover sheet",
            2: "professional engineer",
            3: "p.e. and professional engineer",
        }
        result = match_keywords(text_by_page, ["professional engineer", "p.e."])
        assert result.pages["professional engineer"] == [2, 3]
        assert result.pages["p.e."] == [3]

    def test_pages_sorted_ascending(self):
        text_by_page = {3: "p.e.", 1: "p.e.", 2: "p.e."}
        result = match_keywords(text_by_page, ["p.e."])
        assert result.pages["p.e."] == [1, 2, 3]


class TestDedupAndOrder:
    def test_matched_in_config_order(self):
        result = _match("p.e. and professional engineer", ["p.e.", "professional engineer"])
        assert result.matched == ["p.e.", "professional engineer"]

    def test_no_duplicate_keywords(self):
        result = _match("p.e. p.e. p.e.", ["p.e."])
        assert result.matched == ["p.e."]
        assert result.count == 1


class TestEmptyAndNoMatch:
    def test_empty_text_no_match(self):
        result = _match("", ["p.e."])
        assert result.matched == []
        assert result.pages == {}
        assert result.count == 0

    def test_no_keyword_hits(self):
        result = _match("plain prose about a water system", ["p.e.", "engineer seal"])
        assert result.matched == []
        assert result.count == 0

    def test_empty_keyword_list(self):
        result = _match("anything", [])
        assert result.matched == []
        assert result.count == 0

    def test_empty_text_by_page(self):
        result = match_keywords({}, ["p.e."])
        assert result.matched == []
        assert result.count == 0


class TestMatchStampCluster:
    """The proximity rule: a document is stamped only when an engineer
    designation and a license number appear within max_distance characters
    on the same page. A loose phrase alone -- a signature block's "P.E." or
    form-instruction "license number" -- is not a stamp."""

    def test_cluster_found(self):
        cluster = _cluster("Nathan Wallentine, P.E. ... No. 12338863", ["p.e."])
        assert cluster is not None
        assert cluster.designation == "p.e."
        assert cluster.license_number == "No. 12338863"
        assert cluster.page == 1
        assert cluster.distance < 250

    def test_designation_alone_no_cluster(self):
        # "Jesse Ralphs, P.E." in a signature block has no license number.
        assert _cluster("Jesse Ralphs, P.E.", ["p.e."]) is None

    def test_license_alone_no_cluster(self):
        # "proof professional's name and license number" has no designation
        # (and no digits); a bare license number has no designation either.
        assert _cluster("License No. 123456", ["p.e."]) is None

    def test_beyond_max_distance_no_cluster(self):
        text = "P.E. " + " " * 300 + " No. 123456"
        assert _cluster(text, ["p.e."]) is None

    def test_within_max_distance_cluster(self):
        text = "P.E. " + " " * 100 + " No. 123456"
        cluster = _cluster(text, ["p.e."])
        assert cluster is not None
        assert cluster.distance == 102

    def test_license_pattern_variants(self):
        for text, expected_lic in [
            ("P.E. No. 123456", "No. 123456"),
            ("P.E. License No. 123456", "License No. 123456"),
            ("P.E. State of Utah No. 123456", "State of Utah No. 123456"),
            ("P.E. Utah No. 123456", "Utah No. 123456"),
            ("P.E. License #123456", "License #123456"),
            ("P.E. State of Utah #123456", "State of Utah #123456"),
            ("P.E. lic. no. 123456", "lic. no. 123456"),
        ]:
            cluster = _cluster(text, ["p.e."])
            assert cluster is not None, text
            assert cluster.license_number == expected_lic, text

    def test_water_right_number_no_cluster(self):
        # "APPLICATION/CLAIM NO.: A24354" -- the number has letters, so the
        # license pattern (6-8 digits, whole-number lookarounds) doesn't
        # match; a nearby P.E. therefore forms no cluster.
        text = "P.E. APPLICATION/CLAIM NO.: A24354"
        assert _cluster(text, ["p.e."]) is None

    def test_four_digit_contract_number_no_cluster(self):
        # "Work Release No. 2020" -- 4-digit contract numbers are below the
        # 6-digit floor, so a nearby signature-block P.E. forms no cluster.
        text = "Jesse Ralphs, P.E. Work Release No. 2020"
        assert _cluster(text, ["p.e."]) is None

    def test_water_system_number_no_cluster(self):
        # "System #13060" -- a bare "#" with no license/Utah context is a
        # water-system number, not a PE license; a nearby P.E. forms no
        # cluster. (Six of the 13 false positives on the KCWCD corpus.)
        text = "Scott D. Hacking, P.E. System #13060"
        assert _cluster(text, ["p.e."]) is None

    def test_project_number_no_cluster(self):
        # "Project No: 220638" -- a "no." preceded by a word is a project
        # number, not a PE license.
        text = "John Jacobsen, P.E. Landmark Project No: 220638"
        assert _cluster(text, ["p.e."]) is None

    def test_file_number_no_cluster(self):
        # "File #15254" -- a bare "#" with no license/Utah context.
        text = "J. Paul Wright, P.E. File #15254"
        assert _cluster(text, ["p.e."]) is None

    def test_bidders_license_no_cluster(self):
        # "Bidder's License No.: 233855-5501" -- the "license" prefix is
        # preceded by a word ("Bidder's"), so it is not a PE license.
        text = "Steve Johansen Bidder's License No.: 233855-5501"
        assert _cluster(text, ["p.e."]) is None

    def test_operators_license_no_cluster(self):
        # "License Number: 12891" -- a 5-digit operator's certification is
        # below the 6-digit floor.
        text = "P.E. License Number: 12891"
        assert _cluster(text, ["p.e."]) is None

    def test_bank_account_number_no_cluster(self):
        # "#5110788" -- a bare "#" with no license/Utah context is a bank
        # account number, not a PE license.
        text = "Ken Hoffman, P.E. checking acct #5110788"
        assert _cluster(text, ["p.e."]) is None

    def test_bare_utah_octothorpe_no_cluster(self):
        # "Utah #5110788" -- a bank account typed as "...State Bank of Southern
        # Utah #5110788". The "#" form requires an explicit "State of Utah"
        # prefix; bare "Utah #" is bank-account style (the two live FPs on the
        # KCWCD corpus, which read the account number as a license).
        for text in (
            "Ken Hoffman, P.E. checking acct State Bank of Southern Utah #5110788",
            "Ken Hoffman, P.E. checking acct State Bank of Southern \nUtah #5110788",
            "Ken Hoffman, P.E. checking acct State Bank of Southern\nUtah #5110788",
        ):
            assert _cluster(text, ["p.e."]) is None, text

    def test_snippet_collapses_newlines(self):
        cluster = _cluster("P.E.\nNo. 123456", ["p.e."])
        assert cluster is not None
        assert "\n" not in cluster.snippet
        assert " | " in cluster.snippet

    def test_earliest_page_wins(self):
        # Page 1 has a cluster at distance 202; page 2 has one at distance 1.
        # The earliest page with any cluster wins, not the closest overall.
        text_by_page = {
            1: "P.E. " + " " * 200 + " No. 123456",
            2: "P.E. No. 123456",
        }
        cluster = match_stamp_cluster(text_by_page, ["p.e."])
        assert cluster is not None
        assert cluster.page == 1

    def test_closest_pair_wins_on_page(self):
        text = "P.E. No. 123456 " + " " * 100 + " No. 999999"
        cluster = _cluster(text, ["p.e."])
        assert cluster is not None
        assert cluster.license_number == "No. 123456"

    def test_designation_uses_whole_word_regex(self):
        # The designation side reuses the whole-word-ish regex: "p.e." must
        # not match inside "P.E.123" (alphanumeric continuation).
        assert _cluster("P.E.123 No. 123456", ["p.e."]) is None


class TestProseExclusions:
    """Designation/license candidates inside non-stamp prose are excluded --
    the candidates, not the page: a real seal elsewhere on the same page
    still forms a cluster. Corpus examples are the two live false positives
    from the 2026-09-09 KCWCD run."""

    def test_statute_name_line_no_cluster(self):
        # 21-0817 Clarkson Pre-Plat p.1: a surveyor's certificate quoting the
        # statute; the "license" is the surveyor's, not an engineer's.
        text = (
            "SURVEYOR'S CERTIFICATE\n"
            "I, Thomas W. Avant, a Professional Land Surveyor, License No. 5561917,\n"
            "hold this license in accordance with Title 58, Chapter 22,\n"
            "Professional Engineers and Professional Land Surveyors Licensing Act\n"
        )
        assert _cluster(text, ["professional engineer"]) is None

    def test_instruction_line_no_cluster(self):
        # KCWCD Water Model Report p.12: a blank signature form whose
        # instruction line mentions "professional engineer"; the bare
        # "No. 12338863" belongs to an (unsigned) signature listing.
        # Spacing mirrors the real page: the form field's "P.E." sits >250
        # chars from the license, so the instruction-line designation is the
        # only candidate that could ever cluster there.
        text = (
            "State of Utah P.E. License No.\n"
            "Date\n"
            "(* This page must be signed, sealed, and dated by a professional engineer\n"
            "who oversees the completion of this hydraulic modeling analysis, and who\n"
            "is fully responsible for the accuracy of this hydraulic modeling analysis,\n"
            "as required by the reviewing authority for this water system model.)\n"
            "Nathan Wallentine\n"
            "  No. 12338863\n"
        )
        assert _cluster(text, ["professional engineer", "p.e."]) is None

    def test_instruction_exclusion_is_load_bearing(self, monkeypatch):
        # With the filter disabled, the instruction page clusters (the
        # instruction's "professional engineer" sits within max_distance of
        # the license number) -- the no-cluster fixture above passes only
        # because of the exclusion, not incidentally.
        monkeypatch.setattr(
            keywords,
            "INSTRUCTION_PROSE_PATTERN",
            re.compile(r"(?!)"),  # never matches
        )
        text = (
            "(* This page must be signed, sealed, and dated by a professional engineer\n"
            "who oversees the completion of this hydraulic modeling analysis.)\n"
            "Nathan Wallentine\n"
            "  No. 12338863\n"
        )
        assert _cluster(text, ["professional engineer", "p.e."]) is not None

    def test_surveyor_license_no_cluster(self):
        # A land surveyor's license directly following the surveyor
        # designation is out of scope (engineer stamps only).
        text = "Thomas W. Avant, a Professional Land Surveyor, License No. 5561917, P.E."
        assert _cluster(text, ["p.e."]) is None

    def test_instruction_on_page_does_not_kill_a_real_seal_on_it(self):
        # The filters drop candidates, not pages: a genuine seal elsewhere on
        # the same page still clusters.
        text = (
            "* This page must be signed, sealed, and dated by a professional engineer\n"
            "Dustyn W. Shaffer, P.E.\n"
            "State of Utah No. 343921\n"
        )
        cluster = _cluster(text, ["p.e."])
        assert cluster is not None
        assert cluster.designation == "p.e."

    def test_statute_line_on_page_does_not_kill_a_real_seal_on_it(self):
        text = (
            "Professional Engineers and Professional Land Surveyors Licensing Act\n"
            "Dustyn W. Shaffer, P.E.\n"
            "State of Utah No. 343921\n"
        )
        cluster = _cluster(text, ["p.e.", "professional engineer"])
        assert cluster is not None
        assert cluster.designation == "p.e."

    def test_instruction_on_page_1_does_not_hide_a_seal_on_page_2(self):
        text_by_page = {
            1: "must be signed, sealed, and dated by a professional engineer",
            2: "Dustyn W. Shaffer, P.E. State of Utah No. 343921",
        }
        cluster = match_stamp_cluster(text_by_page, ["p.e.", "professional engineer"])
        assert cluster is not None
        assert cluster.page == 2

    def test_statute_exclusion_is_line_scoped(self):
        # A statute phrase on one line must not exclude a designation on
        # another line of the same page.
        text = (
            "Professional Engineers and Professional Land Surveyors Licensing Act\n"
            "P.E. No. 123456\n"
        )
        assert _cluster(text, ["p.e."]) is not None
