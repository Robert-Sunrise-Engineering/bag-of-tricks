"""Text extraction with OCR fallback -- the I/O-heavy module.

Extracts per-page text from a document: PDFs use the PyMuPDF text layer,
falling back to Tesseract OCR per page when a page has too little text;
non-PDF images (including multi-frame TIFFs) are fully OCRed frame by
frame. Transient file-open failures (network share blips, file locks) are
retried a configurable number of times with a delay before giving up.
``pytesseract.pytesseract.TesseractNotFoundError`` is deliberately
NOT caught here -- it propagates to cli.py, which turns it into a
run-level warning and per-document ERROR rows.
"""

import io
import os
import time
from dataclasses import dataclass

import pymupdf as fitz
import pytesseract
import PIL.Image
import PIL.ImageSequence


# Transient file-open failures -- network-share blips, file locks, a document
# being re-saved mid-scan -- marked 22 documents ERROR in the 2026-09-09 KCWCD
# run that opened fine minutes later (only 1 of the 23 was genuinely corrupt).
# Opening is therefore retried with a delay before giving up, so a transient
# blip does not become a permanent ERROR verdict. Only the open is retried --
# re-running a completed multi-page OCR extraction would double its cost.
DEFAULT_OPEN_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 10.0


@dataclass(frozen=True)
class ExtractionResult:
    """The extracted text for one document.

    Attributes:
        text_by_page: Maps 1-based page numbers to that page's text.
        source_by_page: Maps 1-based page numbers to that page's source
            (``"text_layer"`` / ``"ocr"`` / ``"image_ocr"``).
        page_count: Number of pages/frames extracted.
    """

    text_by_page: dict[int, str]
    source_by_page: dict[int, str]
    page_count: int


def extract_text(path, ocr_settings, extraction_settings=None) -> ExtractionResult:
    """
    Extract per-page text from a document, with OCR fallback.

    Dispatches on file extension: ``.pdf`` goes through the PDF path
    (text layer first, OCR fallback per page); anything else goes through
    the image path (full OCR, frame by frame for multi-frame TIFFs).

    Args:
        path: The document path.
        ocr_settings: The ``ocr`` section of config.json.
        extraction_settings: The optional ``extraction`` section of
            config.json (``open_retries`` / ``retry_delay_seconds``); module
            defaults apply when None or missing keys.

    Returns:
        An ExtractionResult with per-page text, per-page source, and the
        page count.

    Raises:
        ValueError: For an encrypted PDF or a zero-page PDF.
        pytesseract.pytesseract.TesseractNotFoundError: If OCR is needed but
            the Tesseract binary is missing (propagates to cli.py).
        fitz.FileDataError / OSError: If the file cannot be opened after all
            open attempts (transient failures are retried first).
    """
    _configure_tesseract(ocr_settings)
    extraction_settings = extraction_settings or {}
    attempts = max(1, extraction_settings.get("open_retries", DEFAULT_OPEN_RETRIES))
    delay_seconds = extraction_settings.get(
        "retry_delay_seconds", DEFAULT_RETRY_DELAY_SECONDS
    )
    if os.path.splitext(path)[1].lower() == ".pdf":
        return _extract_pdf(path, ocr_settings, attempts, delay_seconds)
    return _extract_image(path, ocr_settings, attempts, delay_seconds)


def _open_with_retry(path, opener, *, attempts, delay_seconds):
    """
    Open ``path`` via ``opener()``, retrying transient open failures.

    Retries only on PyMuPDF/PIL open failures and OSErrors -- the signatures
    of a share blip, a lock, or a file being re-saved mid-scan. A plain
    ValueError (encrypted / zero-page PDF) is deterministic and is not
    retried, nor is anything raised after the open succeeds.

    Args:
        path: The document path (for the final error message).
        opener: A zero-argument callable returning the opened document.
        attempts: Total open attempts (>= 1).
        delay_seconds: Sleep between attempts.

    Returns:
        Whatever ``opener()`` returns.

    Raises:
        The last open failure, with ``(after N open attempt(s))`` appended to
        its message.
    """
    for attempt in range(1, attempts + 1):
        try:
            return opener()
        except (fitz.FileDataError, OSError) as exc:
            if attempt == attempts:
                raise type(exc)(f"{exc} (after {attempts} open attempt(s))") from exc
            time.sleep(delay_seconds)


def _extract_pdf(path, ocr_settings, attempts, delay_seconds) -> ExtractionResult:
    min_chars = ocr_settings["min_chars_for_text_layer"]
    ocr_dpi = ocr_settings["ocr_dpi"]

    doc = _open_with_retry(
        path,
        lambda: fitz.open(path),
        attempts=attempts,
        delay_seconds=delay_seconds,
    )
    with doc:
        if doc.needs_pass:
            raise ValueError(f"Encrypted PDF: {path}")
        if doc.page_count == 0:
            raise ValueError(f"PDF has no pages: {path}")

        text_by_page = {}
        source_by_page = {}
        for i in range(doc.page_count):
            page = doc[i]
            text = page.get_text()
            if _needs_ocr(text, min_chars):
                # Render the page and OCR it. The pixmap is round-tripped
                # through PNG + PIL.Image.open(...).convert("RGB") -- this
                # exact recipe -- because Image.frombytes is a
                # colorspace/alpha minefield (fitz pixmaps come back
                # RGB/RGBA/CMYK) and some Tesseract builds mishandle alpha.
                pix = page.get_pixmap(dpi=ocr_dpi)
                img = PIL.Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                # OCR output REPLACES the sparse text-layer text (presumed
                # noise), rather than being appended to it.
                text = pytesseract.image_to_string(
                    img,
                    lang=ocr_settings["tesseract_lang"],
                    config=_tesseract_config(ocr_settings),
                )
                source = "ocr"
            else:
                source = "text_layer"
            text_by_page[i + 1] = text
            source_by_page[i + 1] = source
        page_count = doc.page_count

    return ExtractionResult(
        text_by_page=text_by_page,
        source_by_page=source_by_page,
        page_count=page_count,
    )


def _extract_image(path, ocr_settings, attempts, delay_seconds) -> ExtractionResult:
    text_by_page = {}
    source_by_page = {}
    page_count = 0
    img = _open_with_retry(
        path,
        lambda: PIL.Image.open(path),
        attempts=attempts,
        delay_seconds=delay_seconds,
    )
    with img:
        # Iterate every frame so multi-frame TIFFs are fully OCRed --
        # reading only frame 1 would silently miss stamps on sheets 2..N of
        # a scan set.
        for i, frame in enumerate(PIL.ImageSequence.Iterator(img)):
            page_count += 1
            text_by_page[i + 1] = pytesseract.image_to_string(
                frame,
                lang=ocr_settings["tesseract_lang"],
                config=_tesseract_config(ocr_settings),
            )
            source_by_page[i + 1] = "image_ocr"
    return ExtractionResult(
        text_by_page=text_by_page,
        source_by_page=source_by_page,
        page_count=page_count,
    )


def _needs_ocr(text: str, min_chars: int) -> bool:
    """
    Return True iff a page's text is too sparse to trust as a text layer.

    A page whose whitespace-stripped text length is below ``min_chars`` is
    presumed to be a scanned image with a stray text layer (or none), so it
    should be OCRed instead.

    Args:
        text: The page's text-layer text.
        min_chars: The ``ocr.min_chars_for_text_layer`` threshold.

    Returns:
        True iff ``len(text.strip()) < min_chars``.
    """
    return len(text.strip()) < min_chars


def aggregate_source(source_by_page: dict[int, str]) -> str:
    """
    Collapse a per-page source map into one document-level source string.

    Args:
        source_by_page: Maps 1-based page numbers to per-page sources.

    Returns:
        The single source value if every page shares it, else ``"mixed"``.
    """
    values = set(source_by_page.values())
    if len(values) == 1:
        return next(iter(values))
    return "mixed"


def _configure_tesseract(ocr_settings) -> None:
    """Wire ``ocr.tesseract_cmd`` into pytesseract when set (e.g. a
    machine-specific Tesseract install path from config.local.json)."""
    tesseract_cmd = ocr_settings.get("tesseract_cmd")
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd


def _tesseract_config(ocr_settings) -> str:
    """Build the pytesseract ``config`` string from ``ocr.tesseract_psm``.

    Default PSM 3 is poor on sparse drawing sheets; 11 (sparse text)
    materially improves as-built OCR, so a configured psm is passed through
    as ``--psm <n>``.
    """
    psm = ocr_settings.get("tesseract_psm")
    if psm is not None:
        return f"--psm {psm}"
    return ""
