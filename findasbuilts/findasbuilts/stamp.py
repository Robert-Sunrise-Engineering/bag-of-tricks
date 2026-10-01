"""Engineer's-stamp detection -- the semantic home of "what is a stamp".

An engineer's stamp (also called a seal) is the mark a licensed professional
engineer applies to a document to certify that the work was prepared under
their responsible charge. In practice it is a text-bearing mark: the
engineer's name, license number, and phrases like "professional engineer",
"license no", or "p.e.". v1 detects the stamp by matching text only -- no
image/circle detection.

A document is stamped only when the evidence *clusters*: an engineer
designation (``p.e.``, ``professional engineer``, ``engineer's seal``, ...)
within ``max_distance`` characters of a license number (``State of Utah
No. 12338863``, ``License #12338863``, ``No. 12338863``) on the same page. A
loose phrase alone -- "Jesse Ralphs, P.E." in a signature block, or "proof
professional's name and license number" in form instructions -- is not a
stamp. See the design doc's scope guardrails.
"""

from findasbuilts.keywords import StampCluster, match_stamp_cluster


def detect_stamp(text_by_page: dict[int, str], stamp_keywords: list[str]) -> StampCluster | None:
    """
    Detect an engineer's stamp in per-page text.

    Thin semantic wrapper over ``keywords.match_stamp_cluster`` -- the
    matching rules (designation + license-number proximity, whole-word-ish
    regex, case-insensitive) all live there; this module owns what the
    keyword list *means*.

    Args:
        text_by_page: Maps 1-based page numbers to that page's extracted text.
        stamp_keywords: The configured ``keywords.stamp`` designation list.

    Returns:
        A StampCluster (the closest designation + license-number pair on the
        earliest page that has one), or None if no stamp evidence was found.
    """
    return match_stamp_cluster(text_by_page, stamp_keywords)
