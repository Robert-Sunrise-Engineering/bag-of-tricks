"""Tests for findasbuilts.extract.

All tests run with no real PDFs, no Tesseract binary, and no network:
extract.fitz / extract.pytesseract / extract.PIL are monkeypatched with
fakes. Covers the text-layer-vs-OCR decision, OCR output replacing sparse
text, multi-frame image iteration, encrypted/zero-page PDF errors, the
_needs_ocr pure helper, aggregate_source, and the tesseract_cmd / --psm
wiring. The cli-level Tesseract-missing handling is covered in
test_cli_main.py.
"""

import io

import pytest

import findasbuilts.extract as extract
from findasbuilts.extract import (
    ExtractionResult,
    _needs_ocr,
    aggregate_source,
    extract_text,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakePixmap:
    def __init__(self, data=b"png-bytes"):
        self._data = data

    def tobytes(self, fmt):
        return self._data


class _FakePage:
    def __init__(self, text, pixmap=None):
        self._text = text
        self._pixmap = pixmap or _FakePixmap()

    def get_text(self):
        return self._text

    def get_pixmap(self, dpi=None):
        return self._pixmap


class _FakeDoc:
    def __init__(self, pages, needs_pass=False):
        self._pages = pages
        self.needs_pass = needs_pass
        self.page_count = len(pages)

    def __getitem__(self, i):
        return self._pages[i]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakePILImage:
    """Stand-in for PIL.Image.open(...).convert("RGB") in the PDF path."""

    def __init__(self):
        self.mode = None

    def convert(self, mode):
        self.mode = mode
        return self


class _FakeFrame:
    def __init__(self, label):
        self.label = label


class _FakeImageFile:
    """Stand-in for PIL.Image.open(path) in the image path."""

    def __init__(self, frames):
        self._frames = frames

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ocr_settings(**over):
    settings = {
        "min_chars_for_text_layer": 20,
        "ocr_dpi": 300,
        "tesseract_lang": "eng",
        "tesseract_psm": None,
        "tesseract_cmd": None,
    }
    settings.update(over)
    return settings


def _install_pdf_fakes(monkeypatch, pages, *, needs_pass=False, ocr_text="OCR RESULT"):
    """Monkeypatch extract.fitz/PIL/pytesseract for the PDF path. Returns a
    list recording every image_to_string call as (img, lang, config)."""
    calls = []

    def fake_image_to_string(img, lang=None, config=None):
        calls.append((img, lang, config))
        return ocr_text

    monkeypatch.setattr(extract.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(extract.PIL.Image, "open", lambda *a, **k: _FakePILImage())
    monkeypatch.setattr(extract.fitz, "open", lambda path: _FakeDoc(pages, needs_pass=needs_pass))
    return calls


def _install_image_fakes(monkeypatch, frames, *, ocr_text="OCR RESULT"):
    """Monkeypatch extract.PIL/pytesseract for the image path. Returns a
    list recording every image_to_string call as (img, lang, config)."""
    calls = []

    def fake_image_to_string(img, lang=None, config=None):
        calls.append((img, lang, config))
        return ocr_text

    monkeypatch.setattr(extract.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(extract.PIL.Image, "open", lambda path: _FakeImageFile(frames))
    monkeypatch.setattr(extract.PIL.ImageSequence, "Iterator", lambda img: iter(img._frames))
    return calls


# ---------------------------------------------------------------------------
# PDF path
# ---------------------------------------------------------------------------


class TestPdfTextLayer:
    def test_page_above_threshold_uses_text_layer_and_skips_ocr(self, monkeypatch):
        calls = _install_pdf_fakes(monkeypatch, [_FakePage("A" * 100)])
        result = extract_text("C:/docs/a.pdf", _ocr_settings())
        assert result == ExtractionResult(
            text_by_page={1: "A" * 100},
            source_by_page={1: "text_layer"},
            page_count=1,
        )
        assert calls == []  # OCR never called

    def test_mixed_pages_tracked_per_page(self, monkeypatch):
        calls = _install_pdf_fakes(
            monkeypatch,
            [_FakePage("A" * 100), _FakePage("sparse"), _FakePage("B" * 50)],
        )
        result = extract_text("C:/docs/mixed.pdf", _ocr_settings())
        assert result.text_by_page == {1: "A" * 100, 2: "OCR RESULT", 3: "B" * 50}
        assert result.source_by_page == {1: "text_layer", 2: "ocr", 3: "text_layer"}
        assert result.page_count == 3
        assert len(calls) == 1  # only the sparse page was OCRed


class TestPdfOcrFallback:
    def test_sparse_page_ocred_and_output_replaces_text(self, monkeypatch):
        calls = _install_pdf_fakes(monkeypatch, [_FakePage("sparse")])
        result = extract_text("C:/docs/scan.pdf", _ocr_settings())
        # OCR output REPLACES the sparse text-layer text, not appended to it.
        assert result.text_by_page == {1: "OCR RESULT"}
        assert result.source_by_page == {1: "ocr"}
        assert len(calls) == 1

    def test_ocr_receives_rgb_converted_png_roundtrip(self, monkeypatch):
        calls = _install_pdf_fakes(monkeypatch, [_FakePage("sparse")])
        extract_text("C:/docs/scan.pdf", _ocr_settings())
        img, lang, config = calls[0]
        # The image passed to OCR is the .convert("RGB") result of the
        # PIL.Image.open(io.BytesIO(pix.tobytes("png"))) round-trip.
        assert isinstance(img, _FakePILImage)
        assert img.mode == "RGB"
        assert lang == "eng"

    def test_ocr_dpi_passed_to_pixmap(self, monkeypatch):
        seen = {}

        class _SpyPixmap(_FakePixmap):
            pass

        class _SpyPage(_FakePage):
            def get_pixmap(self, dpi=None):
                seen["dpi"] = dpi
                return _SpyPixmap()

        _install_pdf_fakes(monkeypatch, [_SpyPage("sparse")])
        extract_text("C:/docs/scan.pdf", _ocr_settings(ocr_dpi=600))
        assert seen["dpi"] == 600


class TestPdfErrors:
    def test_encrypted_pdf_raises(self, monkeypatch):
        _install_pdf_fakes(monkeypatch, [_FakePage("A" * 100)], needs_pass=True)
        with pytest.raises(ValueError, match="Encrypted"):
            extract_text("C:/docs/enc.pdf", _ocr_settings())

    def test_zero_page_pdf_raises(self, monkeypatch):
        _install_pdf_fakes(monkeypatch, [])
        with pytest.raises(ValueError, match="no pages"):
            extract_text("C:/docs/empty.pdf", _ocr_settings())


# ---------------------------------------------------------------------------
# Image path
# ---------------------------------------------------------------------------


class TestImagePath:
    def test_single_frame_image_ocred(self, monkeypatch):
        calls = _install_image_fakes(monkeypatch, [_FakeFrame("f1")])
        result = extract_text("C:/docs/photo.png", _ocr_settings())
        assert result == ExtractionResult(
            text_by_page={1: "OCR RESULT"},
            source_by_page={1: "image_ocr"},
            page_count=1,
        )
        assert len(calls) == 1

    def test_multi_frame_tiff_fully_ocred(self, monkeypatch):
        calls = _install_image_fakes(monkeypatch, [_FakeFrame("f1"), _FakeFrame("f2")])
        result = extract_text("C:/docs/set.tif", _ocr_settings())
        assert result.text_by_page == {1: "OCR RESULT", 2: "OCR RESULT"}
        assert result.source_by_page == {1: "image_ocr", 2: "image_ocr"}
        assert result.page_count == 2
        assert len(calls) == 2  # every frame OCRed, not just frame 1

    def test_frames_passed_to_ocr_in_order(self, monkeypatch):
        calls = _install_image_fakes(monkeypatch, [_FakeFrame("f1"), _FakeFrame("f2")])
        extract_text("C:/docs/set.tif", _ocr_settings())
        assert [c[0].label for c in calls] == ["f1", "f2"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestNeedsOcr:
    def test_below_threshold_needs_ocr(self):
        assert _needs_ocr("sparse", 20) is True

    def test_above_threshold_does_not_need_ocr(self):
        assert _needs_ocr("A" * 100, 20) is False

    def test_whitespace_only_needs_ocr(self):
        assert _needs_ocr("   \n  ", 20) is True

    def test_empty_needs_ocr(self):
        assert _needs_ocr("", 20) is True

    def test_exactly_at_threshold_does_not_need_ocr(self):
        assert _needs_ocr("A" * 20, 20) is False


class TestAggregateSource:
    def test_all_text_layer(self):
        assert aggregate_source({1: "text_layer", 2: "text_layer"}) == "text_layer"

    def test_all_ocr(self):
        assert aggregate_source({1: "ocr", 2: "ocr"}) == "ocr"

    def test_all_image_ocr(self):
        assert aggregate_source({1: "image_ocr"}) == "image_ocr"

    def test_mixed(self):
        assert aggregate_source({1: "text_layer", 2: "ocr"}) == "mixed"

    def test_empty_is_mixed(self):
        assert aggregate_source({}) == "mixed"


# ---------------------------------------------------------------------------
# Tesseract wiring
# ---------------------------------------------------------------------------


class TestTesseractWiring:
    def test_tesseract_cmd_wired_when_set(self, monkeypatch):
        monkeypatch.setattr(extract.pytesseract.pytesseract, "tesseract_cmd", "original")
        _install_pdf_fakes(monkeypatch, [_FakePage("sparse")])
        extract_text(
            "C:/docs/scan.pdf",
            _ocr_settings(tesseract_cmd="C:\\Program Files\\Tesseract-OCR\\tesseract.exe"),
        )
        assert (
            extract.pytesseract.pytesseract.tesseract_cmd
            == "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
        )

    def test_tesseract_cmd_not_touched_when_unset(self, monkeypatch):
        monkeypatch.setattr(extract.pytesseract.pytesseract, "tesseract_cmd", "original")
        _install_pdf_fakes(monkeypatch, [_FakePage("sparse")])
        extract_text("C:/docs/scan.pdf", _ocr_settings(tesseract_cmd=None))
        assert extract.pytesseract.pytesseract.tesseract_cmd == "original"

    def test_psm_passed_as_config(self, monkeypatch):
        calls = _install_pdf_fakes(monkeypatch, [_FakePage("sparse")])
        extract_text("C:/docs/scan.pdf", _ocr_settings(tesseract_psm=11))
        assert calls[0][2] == "--psm 11"

    def test_no_psm_means_empty_config(self, monkeypatch):
        calls = _install_pdf_fakes(monkeypatch, [_FakePage("sparse")])
        extract_text("C:/docs/scan.pdf", _ocr_settings(tesseract_psm=None))
        assert calls[0][2] == ""

    def test_psm_passed_for_images_too(self, monkeypatch):
        calls = _install_image_fakes(monkeypatch, [_FakeFrame("f1")])
        extract_text("C:/docs/set.tif", _ocr_settings(tesseract_psm=11))
        assert calls[0][2] == "--psm 11"


# ---------------------------------------------------------------------------
# Open retry
# ---------------------------------------------------------------------------


class TestOpenRetry:
    """Transient file-open failures (share blips, locks, files re-saved
    mid-scan) are retried; deterministic ValueErrors are not."""

    def _install_silent_sleep(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(extract.time, "sleep", recorded.append)
        return recorded

    def test_transient_pdf_open_failure_retried_and_recovers(self, monkeypatch):
        calls = {"n": 0}

        def flaky_open(path):
            calls["n"] += 1
            if calls["n"] < 3:
                raise extract.fitz.FileDataError("Failed to open file as type pdf.")
            return _FakeDoc([_FakePage("A" * 100)])

        monkeypatch.setattr(extract.fitz, "open", flaky_open)
        monkeypatch.setattr(extract.PIL.Image, "open", lambda *a, **k: _FakePILImage())
        monkeypatch.setattr(extract.pytesseract, "image_to_string", lambda *a, **k: "")
        self._install_silent_sleep(monkeypatch)

        result = extract_text(
            "C:/docs/flaky.pdf",
            _ocr_settings(),
            {"open_retries": 3, "retry_delay_seconds": 5},
        )
        assert result.page_count == 1
        assert calls["n"] == 3  # opened on the third attempt

    def test_transient_image_open_failure_retried_and_recovers(self, monkeypatch):
        calls = {"n": 0}

        def flaky_open(path):
            calls["n"] += 1
            if calls["n"] < 2:
                raise OSError("share blip")
            return _FakeImageFile([_FakeFrame("f1")])

        monkeypatch.setattr(extract.PIL.Image, "open", flaky_open)
        monkeypatch.setattr(extract.PIL.ImageSequence, "Iterator", lambda img: iter(img._frames))
        monkeypatch.setattr(extract.pytesseract, "image_to_string", lambda *a, **k: "OCR RESULT")
        sleeps = self._install_silent_sleep(monkeypatch)

        result = extract_text(
            "C:/docs/flaky.png", _ocr_settings(), {"open_retries": 3, "retry_delay_seconds": 5}
        )
        assert result.page_count == 1
        assert calls["n"] == 2
        assert sleeps == [5]  # one delay between the two attempts

    def test_permanent_open_failure_raises_after_all_attempts(self, monkeypatch):
        calls = {"n": 0}

        def broken_open(path):
            calls["n"] += 1
            raise extract.fitz.FileDataError("Failed to open file as type pdf.")

        monkeypatch.setattr(extract.fitz, "open", broken_open)
        monkeypatch.setattr(extract.PIL.Image, "open", lambda *a, **k: _FakePILImage())
        monkeypatch.setattr(extract.pytesseract, "image_to_string", lambda *a, **k: "")
        self._install_silent_sleep(monkeypatch)

        with pytest.raises(
            extract.fitz.FileDataError, match=r"after 3 open attempt\(s\)"
        ):
            extract_text(
                "C:/docs/bad.pdf", _ocr_settings(), {"open_retries": 3, "retry_delay_seconds": 0}
            )
        assert calls["n"] == 3

    def test_defaults_apply_when_extraction_settings_none(self, monkeypatch):
        calls = {"n": 0}

        def broken_open(path):
            calls["n"] += 1
            raise OSError("locked")

        monkeypatch.setattr(extract.fitz, "open", broken_open)
        monkeypatch.setattr(extract.PIL.Image, "open", lambda *a, **k: _FakePILImage())
        monkeypatch.setattr(extract.pytesseract, "image_to_string", lambda *a, **k: "")
        sleeps = self._install_silent_sleep(monkeypatch)

        with pytest.raises(OSError, match=r"after 3 open attempt\(s\)"):
            extract_text("C:/docs/locked.pdf", _ocr_settings())
        assert calls["n"] == 3
        assert sleeps == [extract.DEFAULT_RETRY_DELAY_SECONDS] * 2

    def test_deterministic_valueerror_not_retried(self, monkeypatch):
        # An opener ValueError (encrypted / zero-page PDF upstream) is
        # deterministic: one attempt, no sleeps, immediate raise.
        calls = {"n": 0}

        def rejecting_open(path):
            calls["n"] += 1
            raise ValueError("PDF has no pages")

        monkeypatch.setattr(extract.fitz, "open", rejecting_open)
        monkeypatch.setattr(extract.PIL.Image, "open", lambda *a, **k: _FakePILImage())
        sleeps = self._install_silent_sleep(monkeypatch)

        with pytest.raises(ValueError, match="no pages"):
            extract_text("C:/docs/empty.pdf", _ocr_settings(), {"open_retries": 3})
        assert calls["n"] == 1
        assert sleeps == []

    def test_open_retries_zero_means_one_attempt(self, monkeypatch):
        calls = {"n": 0}

        def broken_open(path):
            calls["n"] += 1
            raise extract.fitz.FileDataError("corrupt")

        monkeypatch.setattr(extract.fitz, "open", broken_open)
        monkeypatch.setattr(extract.PIL.Image, "open", lambda *a, **k: _FakePILImage())
        monkeypatch.setattr(extract.pytesseract, "image_to_string", lambda *a, **k: "")
        self._install_silent_sleep(monkeypatch)

        with pytest.raises(extract.fitz.FileDataError, match=r"after 1 open attempt\(s\)"):
            extract_text("C:/docs/bad.pdf", _ocr_settings(), {"open_retries": 0})
        assert calls["n"] == 1
