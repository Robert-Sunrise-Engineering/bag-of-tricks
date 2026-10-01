"""Document verdicts -- the pure decision layer.

Turns a stamp-cluster result into a per-document verdict: STAMP, NO_STAMP,
or (set by cli.py when extraction fails) ERROR.
"""

from dataclasses import dataclass

from findasbuilts.keywords import StampCluster


@dataclass(frozen=True)
class Verdict:
    """The verdict for one document.

    Attributes:
        path: The document's path.
        has_stamp: True iff stamp evidence was found.
        verdict: ``"STAMP"``, ``"NO_STAMP"``, or ``"ERROR"``.
        stamp_keywords: The matched designation keyword(s), or [].
        stamp_pages: Maps each matched designation to its 1-based pages, or {}.
        page_count: Number of pages/frames extracted.
        text_source: ``"text_layer"`` / ``"ocr"`` / ``"mixed"`` / ``"image_ocr"``.
        evidence: The matched text snippet around the stamp cluster (for
            human verification); empty unless verdict is ``"STAMP"``.
        error: Error message; non-empty only when verdict is ``"ERROR"``.
    """

    path: str
    has_stamp: bool
    verdict: str
    stamp_keywords: list[str]
    stamp_pages: dict[str, list[int]]
    page_count: int
    text_source: str
    evidence: str
    error: str


def evaluate_document(
    path,
    stamp: StampCluster | None,
    *,
    page_count: int,
    text_source: str,
) -> Verdict:
    """
    Evaluate one document's stamp cluster into a Verdict.

    Args:
        path: The document's path.
        stamp: The StampCluster from ``stamp.detect_stamp``, or None.
        page_count: Number of pages/frames extracted.
        text_source: The aggregate text source for the document.

    Returns:
        A Verdict with verdict ``"STAMP"`` if a stamp cluster was found,
        else ``"NO_STAMP"``. ``error`` is always empty here -- ERROR verdicts
        are constructed by cli.py when extraction raises.
    """
    has_stamp = stamp is not None
    return Verdict(
        path=path,
        has_stamp=has_stamp,
        verdict="STAMP" if has_stamp else "NO_STAMP",
        stamp_keywords=[stamp.designation] if stamp else [],
        stamp_pages={stamp.designation: [stamp.page]} if stamp else {},
        page_count=page_count,
        text_source=text_source,
        evidence=stamp.snippet if stamp else "",
        error="",
    )
