"""Pure keyword matching -- the correctness core of findasbuilts.

Given per-page extracted text and a keyword list, reports which keywords
appear and on which pages. No file I/O, no OCR -- just regex matching.

Two matchers live here:

* ``match_keywords`` -- the general whole-word-ish matcher. Any keyword hit
  anywhere counts; ``KeywordMatch.matched`` is an *evidence inventory*.
* ``match_stamp_cluster`` -- the stamp-specific matcher. A document is
  stamped only when an engineer designation and a license number appear
  within ``max_distance`` characters on the same page -- the shape a real
  engineer's seal takes. This is what ``stamp.detect_stamp`` uses.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordMatch:
    """The result of matching a keyword list against per-page text.

    Attributes:
        matched: The keywords that matched, in config order, deduped. This is
            an *evidence inventory*, not a best-match selection -- overlapping
            keywords (e.g. ``professional engineer`` inside ``registered
            professional engineer``) both appear when both match.
        pages: Maps each matched keyword to the 1-based page numbers it
            appeared on.
        count: The number of distinct matched keywords (``len(matched)``).
    """

    matched: list[str]
    pages: dict[str, list[int]]
    count: int


@dataclass(frozen=True)
class StampCluster:
    """A detected stamp cluster: an engineer designation near a license number.

    A real engineer's seal is a cluster of evidence on one page -- an
    engineer designation (``p.e.``, ``professional engineer``, ``engineer's
    seal``, ...) within ``max_distance`` characters of a license number
    (``State of Utah No. 12338863``, ``License #12338863``, ``No. 12338863``).
    Requiring that proximity is what separates a stamp from a loose phrase:
    "Jesse Ralphs, P.E." in a signature block has no license number nearby,
    and "proof professional's name and license number" has no designation
    nearby.

    Attributes:
        designation: The matched designation keyword (e.g. ``"p.e."``).
        license_number: The matched license-number text (e.g. ``"No. 12338863"``).
        page: The 1-based page the cluster appears on.
        distance: The number of characters between the designation and the
            license number.
        snippet: The evidence text around the cluster (newlines collapsed),
            for human verification.
    """

    designation: str
    license_number: str
    page: int
    distance: int
    snippet: str


# A PE license number: a "Utah"/"State of Utah" prefix, or a "license"/"lic."
# prefix not preceded by a word (rejects "Bidder's License"), or a bare
# "no."/"number" not preceded by a word (rejects "Project No"), then
# "no."/"number"/"#", then 6-8 digits. Matches "State of Utah No. 12338863",
# "Utah No. 4777017-2202", "License #12338863", "No. 12338863". The context
# requirement and the 6-8 digit floor keep it from matching water-system
# numbers ("System #13060"), project numbers ("Project No: 220638"), file
# numbers ("File #15254"), bidder's/operator's licenses, and bank accounts
# ("#5110788") that would otherwise false-positive when near a "P.E.". Real
# PE license numbers on the KCWCD corpus are 6-8 digits.
#
# The "#" form requires an explicit "State of Utah" prefix (not bare "Utah"):
# a bank account read as "...State Bank of Southern\nUtah #5110788" -- where a
# text-layer line break splits the phrase -- would otherwise let bare
# "Utah #" match the account number near a signature's "P.E.". Bare-Utah
# license numbers on the corpus are always typed "Utah No. 4777017-2202".
LICENSE_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:"
    r"state\s+of\s+utah\s+(?:no\.?|number|#)"                # "State of Utah No. 12345" / "State of Utah #12345"
    r"|"
    r"utah\s+(?:no\.?|number)"                                # "Utah No. 12345" (bare "Utah #..." is bank-account style)
    r"|"
    r"(?<![A-Za-z']\s)lic(?:ense|\.?)\s+(?:no\.?|number|#)"  # "License #12345" (not "Bidder's License")
    r"|"
    r"(?<![A-Za-z]\s)(?:no\.?|number)"                        # bare "No. 12345" (not "Project No")
    r")"
    r"\s*[:#]?\s*\d{6,8}(?!\d)",
    re.IGNORECASE,
)

# Default maximum distance (in characters) between a designation and a license
# number for them to count as one stamp cluster. 250 (not 300) because a
# contract-number false positive ("Work Release No. 2020" near a signature
# block's "P.E.") sits at ~296 chars on the KCWCD corpus.
DEFAULT_MAX_CLUSTER_DISTANCE = 250

# Two line-shaped texts mention an engineer designation without being one, so
# their designation candidates are dropped (the candidates, not the page: a
# real seal elsewhere on the same page still forms a cluster). Both were live
# false positives in the 2026-09-09 KCWCD run.
#
# The statute name: "...a Professional Land Surveyor, License No. 5561917, hold
# this license in accordance with ... Professional Engineers and Professional
# Land Surveyors Licensing Act" (21-0817 Clarkson Pre-Plat) -- the surveyor's
# own certificate quotes the statute, and "professional engineers" there is
# not a designation.
STATUTE_PROSE_PATTERN = re.compile(
    r"and\s+professional\s+land\s+surveyors",
    re.IGNORECASE,
)

# Certification-page instruction text: "* This page must be signed, sealed, and
# dated by a professional engineer who oversees..." (KCWCD Water Model Report
# p.12) -- an instruction to sign, not a designation. The license number it
# clustered with (dist 141) belonged to a blank signature-form field.
INSTRUCTION_PROSE_PATTERN = re.compile(
    r"must\s+be\s+(?:signed|sealed|dated)",
    re.IGNORECASE,
)

# A land surveyor's license directly following the surveyor designation
# ("a Professional Land Surveyor, License No. 5561917") is not an engineer's
# seal -- outside findasbuilts' scope (engineer stamps only). The tight
# 40-char window keeps a real PE license on a page that merely mentions
# surveying from being dropped.
SURVEYOR_LICENSE_CONTEXT_PATTERN = re.compile(
    r"land\s+surveyor[^\n]{0,40}$",
    re.IGNORECASE,
)


def _compile_keyword_pattern(keyword: str) -> re.Pattern:
    """Compile a keyword into the whole-word-ish, case-insensitive regex used
    by both ``match_keywords`` and ``match_stamp_cluster``.

    ``(?<![A-Za-z0-9])<escaped keyword>(?:s)?(?![A-Za-z0-9])`` -- the
    lookarounds give whole-word matching (``valve`` does not match inside
    ``valvebox``), the optional ``(?:s)?`` handles plurals, and ``p.e.``
    matches at line end and before punctuation (``,``, ``.``, ``\\n`` all
    fall outside the trailing lookahead).
    """
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?:s)?(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def match_keywords(text_by_page: dict[int, str], keywords: list[str]) -> KeywordMatch:
    """
    Match a keyword list against per-page text.

    Each keyword is compiled once into the whole-word-ish regex from
    ``_compile_keyword_pattern``. Any hit anywhere counts -- this is the
    general matcher, not the stamp-specific one (see ``match_stamp_cluster``).

    Args:
        text_by_page: Maps 1-based page numbers to that page's extracted text.
        keywords: The keyword list to search for (config order).

    Returns:
        A KeywordMatch with the matched keywords (config order, deduped),
        their pages, and the match count.
    """
    patterns = [(kw, _compile_keyword_pattern(kw)) for kw in keywords]

    matched = []
    pages = {}
    for kw, pattern in patterns:
        kw_pages = []
        for page_num, text in sorted(text_by_page.items()):
            if pattern.search(text):
                kw_pages.append(page_num)
        if kw_pages:
            matched.append(kw)
            pages[kw] = kw_pages

    return KeywordMatch(matched=matched, pages=pages, count=len(matched))


def _containing_line(text: str, start: int, end: int) -> str:
    """Return the newline-bounded line of ``text`` containing ``text[start:end]``.

    Line-based because both exclusion targets are line-shaped in the corpus,
    whether the text came from the PDF text layer or from OCR.
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end]


def match_stamp_cluster(
    text_by_page: dict[int, str],
    designations: list[str],
    max_distance: int = DEFAULT_MAX_CLUSTER_DISTANCE,
) -> StampCluster | None:
    """
    Detect a stamp cluster: an engineer designation near a license number.

    A real engineer's seal is a cluster of evidence on one page -- an engineer
    designation (``p.e.``, ``professional engineer``, ``engineer's seal``, ...)
    within ``max_distance`` characters of a license number (``State of Utah
    No. 12338863``, ``License #12338863``, ``No. 12338863``). Requiring that
    proximity distinguishes a stamp from a loose phrase: "Jesse Ralphs, P.E."
    in a signature block has no license number nearby, and "proof
    professional's name and license number" has no designation nearby.

    Designation candidates whose line is statute-name or sign-instruction prose,
    and license-number candidates directly following a "land surveyor"
    designation, are excluded -- see the module-level ``*_PATTERN`` comments.

    Args:
        text_by_page: Maps 1-based page numbers to that page's extracted text.
        designations: The designation keyword list (config order).
        max_distance: Maximum characters between a designation and a license
            number for them to count as one cluster.

    Returns:
        The closest cluster on the earliest page that has one, or None.
    """
    patterns = [(kw, _compile_keyword_pattern(kw)) for kw in designations]
    for page_num, text in sorted(text_by_page.items()):
        designation_pos = []
        for kw, pattern in patterns:
            for m in pattern.finditer(text):
                line = _containing_line(text, m.start(), m.end())
                if STATUTE_PROSE_PATTERN.search(line):
                    continue
                if INSTRUCTION_PROSE_PATTERN.search(line):
                    continue
                designation_pos.append((m.start(), m.end(), kw))
        license_pos = []
        for m in LICENSE_NUMBER_PATTERN.finditer(text):
            before = text[max(0, m.start() - 40):m.start()]
            if SURVEYOR_LICENSE_CONTEXT_PATTERN.search(before):
                continue
            license_pos.append((m.start(), m.end(), m.group(0)))

        best = None
        for ds, de, kw in designation_pos:
            for ls, le, lic in license_pos:
                dist = min(abs(ds - le), abs(ls - de))
                if dist <= max_distance and (best is None or dist < best[0]):
                    best = (dist, kw, lic, ds, de, ls, le)
        if best is not None:
            dist, kw, lic, ds, de, ls, le = best
            lo = min(ds, ls)
            hi = max(de, le)
            snippet = text[max(0, lo - 80):hi + 80].replace("\n", " | ")
            return StampCluster(
                designation=kw,
                license_number=lic,
                page=page_num,
                distance=dist,
                snippet=snippet,
            )
    return None
