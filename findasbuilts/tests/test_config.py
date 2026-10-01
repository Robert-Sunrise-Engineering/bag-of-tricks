"""Tests for findasbuilts.config.

Covers load_config (file read / JSON parse errors), load_config_with_local
(the sibling <name>.local.json overlay), and validate_config's whole-file
accept/reject matrix. There is no save_config in v1 (nothing writes config),
so there are no save_config tests here -- the sibling data-conflation
project's save_config byte-parity tests don't apply.
"""

import json

import pytest

from findasbuilts.config import (
    _deep_merge,
    _local_config_path,
    load_config,
    load_config_with_local,
    validate_config,
)


def _valid_cfg(**over):
    cfg = {
        "file_extensions": [".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg"],
        "ocr": {
            "min_chars_for_text_layer": 20,
            "ocr_dpi": 300,
            "tesseract_lang": "eng",
            "tesseract_psm": None,
            "tesseract_cmd": None,
        },
        "keywords": {"stamp": ["professional engineer", "p.e."]},
    }
    cfg.update(over)
    return cfg


class TestLoadConfig:
    def test_loads_valid_json(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(_valid_cfg()), encoding="utf-8")
        assert load_config(str(path)) == _valid_cfg()

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "nope.json"))

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_config(str(path))


class TestLocalConfigPath:
    def test_inserts_local_before_extension(self):
        assert _local_config_path("config.json") == "config.local.json"
        assert _local_config_path("some/dir/config.json") == "some/dir/config.local.json"

    def test_no_extension_appends_local(self):
        assert _local_config_path("config") == "config.local"


class TestDeepMerge:
    def test_scalar_override_wins(self):
        assert _deep_merge({"a": 1, "b": 2}, {"a": 9}) == {"a": 9, "b": 2}

    def test_nested_dicts_merge_recursively(self):
        base = {"ocr": {"dpi": 300, "lang": "eng", "cmd": None}}
        over = {"ocr": {"cmd": "C:/tess/tesseract.exe"}}
        assert _deep_merge(base, over) == {
            "ocr": {"dpi": 300, "lang": "eng", "cmd": "C:/tess/tesseract.exe"},
        }

    def test_list_is_replaced_not_concatenated(self):
        base = {"keywords": {"stamp": ["a", "b"]}}
        over = {"keywords": {"stamp": ["c"]}}
        assert _deep_merge(base, over) == {"keywords": {"stamp": ["c"]}}

    def test_new_key_added(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


class TestLoadConfigWithLocal:
    def test_no_local_file_returns_base(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(_valid_cfg()), encoding="utf-8")
        assert load_config_with_local(str(path)) == _valid_cfg()

    def test_local_overrides_base_deep(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(_valid_cfg()), encoding="utf-8")
        local = tmp_path / "config.local.json"
        local.write_text(
            json.dumps({"ocr": {"tesseract_cmd": "C:/tess/tesseract.exe"}}),
            encoding="utf-8",
        )
        merged = load_config_with_local(str(path))
        assert merged["ocr"]["tesseract_cmd"] == "C:/tess/tesseract.exe"
        # Unmentioned base keys survive the partial local overlay.
        assert merged["ocr"]["min_chars_for_text_layer"] == 20
        assert merged["ocr"]["ocr_dpi"] == 300
        assert merged["keywords"]["stamp"] == ["professional engineer", "p.e."]

    def test_local_list_replaces_base(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(_valid_cfg()), encoding="utf-8")
        local = tmp_path / "config.local.json"
        local.write_text(
            json.dumps({"file_extensions": [".pdf"]}), encoding="utf-8"
        )
        merged = load_config_with_local(str(path))
        assert merged["file_extensions"] == [".pdf"]

    def test_missing_base_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config_with_local(str(tmp_path / "nope.json"))

    def test_invalid_local_json_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(_valid_cfg()), encoding="utf-8")
        local = tmp_path / "config.local.json"
        local.write_text("{ not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_config_with_local(str(path))

    def test_non_object_local_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(_valid_cfg()), encoding="utf-8")
        local = tmp_path / "config.local.json"
        local.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="Local config must be a JSON object"):
            load_config_with_local(str(path))


class TestValidateConfig:
    def test_valid_config_passes(self):
        validate_config(_valid_cfg())  # no raise

    def test_jobs_positive_int_accepted(self):
        for jobs in (1, 8):
            validate_config(_valid_cfg(jobs=jobs))  # no raise

    def test_jobs_absent_or_none_is_ok(self):
        validate_config(_valid_cfg())           # no raise (auto-detect default)
        validate_config(_valid_cfg(jobs=None))  # no raise

    @pytest.mark.parametrize("bad", [0, -1, 1.5, "4", True])
    def test_jobs_non_positive_or_wrong_type_rejected(self, bad):
        with pytest.raises(ValueError, match="jobs"):
            validate_config(_valid_cfg(jobs=bad))

    @pytest.mark.parametrize("missing", ["file_extensions", "ocr", "keywords"])
    def test_missing_top_level_key_rejected(self, missing):
        cfg = _valid_cfg()
        del cfg[missing]
        with pytest.raises(ValueError, match=missing):
            validate_config(cfg)

    def test_empty_extensions_rejected(self):
        with pytest.raises(ValueError, match="file_extensions"):
            validate_config(_valid_cfg(file_extensions=[]))

    def test_non_list_extensions_rejected(self):
        with pytest.raises(ValueError, match="file_extensions"):
            validate_config(_valid_cfg(file_extensions=".pdf"))

    def test_extension_without_dot_rejected(self):
        with pytest.raises(ValueError, match="file_extensions"):
            validate_config(_valid_cfg(file_extensions=["pdf"]))

    def test_non_string_extension_rejected(self):
        with pytest.raises(ValueError, match="file_extensions"):
            validate_config(_valid_cfg(file_extensions=[".pdf", 3]))

    @pytest.mark.parametrize("bad", [0, -1, 1.5, "20", True, None])
    def test_min_chars_non_positive_or_wrong_type_rejected(self, bad):
        ocr = dict(_valid_cfg()["ocr"], min_chars_for_text_layer=bad)
        with pytest.raises(ValueError, match="min_chars_for_text_layer"):
            validate_config(_valid_cfg(ocr=ocr))

    @pytest.mark.parametrize("bad", [0, -300, 300.5, "300", False, None])
    def test_ocr_dpi_non_positive_or_wrong_type_rejected(self, bad):
        ocr = dict(_valid_cfg()["ocr"], ocr_dpi=bad)
        with pytest.raises(ValueError, match="ocr_dpi"):
            validate_config(_valid_cfg(ocr=ocr))

    @pytest.mark.parametrize("bad", ["", "   ", 5, None])
    def test_tesseract_lang_empty_or_wrong_type_rejected(self, bad):
        ocr = dict(_valid_cfg()["ocr"], tesseract_lang=bad)
        with pytest.raises(ValueError, match="tesseract_lang"):
            validate_config(_valid_cfg(ocr=ocr))

    @pytest.mark.parametrize("bad", ["11", 1.5, True, "x"])
    def test_tesseract_psm_wrong_type_rejected(self, bad):
        ocr = dict(_valid_cfg()["ocr"], tesseract_psm=bad)
        with pytest.raises(ValueError, match="tesseract_psm"):
            validate_config(_valid_cfg(ocr=ocr))

    def test_tesseract_psm_int_or_none_accepted(self):
        for psm in (None, 11, 3):
            ocr = dict(_valid_cfg()["ocr"], tesseract_psm=psm)
            validate_config(_valid_cfg(ocr=ocr))  # no raise

    @pytest.mark.parametrize("bad", [5, True, ["x"]])
    def test_tesseract_cmd_wrong_type_rejected(self, bad):
        ocr = dict(_valid_cfg()["ocr"], tesseract_cmd=bad)
        with pytest.raises(ValueError, match="tesseract_cmd"):
            validate_config(_valid_cfg(ocr=ocr))

    def test_tesseract_cmd_string_or_none_accepted(self):
        for cmd in (None, "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"):
            ocr = dict(_valid_cfg()["ocr"], tesseract_cmd=cmd)
            validate_config(_valid_cfg(ocr=ocr))  # no raise

    def test_empty_stamp_list_rejected(self):
        with pytest.raises(ValueError, match="keywords.stamp"):
            validate_config(_valid_cfg(keywords={"stamp": []}))

    def test_non_list_stamp_rejected(self):
        with pytest.raises(ValueError, match="keywords.stamp"):
            validate_config(_valid_cfg(keywords={"stamp": "p.e."}))

    def test_empty_stamp_entry_rejected(self):
        with pytest.raises(ValueError, match="keywords.stamp"):
            validate_config(_valid_cfg(keywords={"stamp": ["p.e.", ""]}))

    def test_non_string_stamp_entry_rejected(self):
        with pytest.raises(ValueError, match="keywords.stamp"):
            validate_config(_valid_cfg(keywords={"stamp": ["p.e.", 3]}))


class TestExtractionSection:
    """The optional ``extraction`` section (open-retry knobs)."""

    def test_valid_extraction_section_passes(self):
        validate_config(
            _valid_cfg(extraction={"open_retries": 3, "retry_delay_seconds": 10})
        )  # no raise

    def test_absent_or_none_extraction_is_ok(self):
        validate_config(_valid_cfg())  # no raise
        validate_config(_valid_cfg(extraction=None))  # no raise

    def test_empty_extraction_object_is_ok(self):
        validate_config(_valid_cfg(extraction={}))  # no raise

    def test_retries_zero_accepted(self):
        validate_config(_valid_cfg(extraction={"open_retries": 0}))  # no raise

    def test_open_retries_wrong_type_or_negative_rejected(self):
        for bad in (-1, True, "3", 2.5, [3]):
            with pytest.raises(ValueError, match="extraction.open_retries"):
                validate_config(_valid_cfg(extraction={"open_retries": bad}))

    def test_retry_delay_wrong_type_or_non_positive_rejected(self):
        for bad in (0, -5, True, "10"):
            with pytest.raises(ValueError, match="extraction.retry_delay_seconds"):
                validate_config(_valid_cfg(extraction={"retry_delay_seconds": bad}))

    def test_retry_delay_float_accepted(self):
        validate_config(
            _valid_cfg(extraction={"retry_delay_seconds": 2.5})
        )  # no raise

    def test_non_object_extraction_rejected(self):
        with pytest.raises(ValueError, match="extraction"):
            validate_config(_valid_cfg(extraction=3))
