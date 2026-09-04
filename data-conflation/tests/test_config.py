"""Tests for conflate.config.save_config.

config.json is CRLF with no trailing newline and 2-space indent; save_config
must reproduce that byte-style exactly so a no-op auto-configure re-run
produces a clean ``git diff`` (the "unchanged" bucket is meaningless
otherwise). These tests pin that contract.
"""

import json
import os

import pytest

from conflate.config import save_config, load_config, validate_layer_config


def test_save_config_writes_valid_indent2_json(tmp_path):
    config = {"layers": {"a": {"authoritative_url": "A/0", "captured_url": "C/0",
                                "match_threshold_m": 1.0, "field_map": {}, "copy_attachments": True}}}
    path = tmp_path / "config.json"
    save_config(str(path), config)

    # Round-trips through load_config and matches the input.
    assert load_config(str(path)) == config
    # 2-space indent (the first nested key is indented exactly 4 spaces:
    # 2 for "layers"'s value, 2 for the entry under it).
    text = path.read_text(encoding="utf-8")
    assert '    "authoritative_url"' in text


def test_save_config_uses_crlf_line_endings(tmp_path):
    path = tmp_path / "config.json"
    save_config(str(path), {"layers": {}})

    raw = path.read_bytes()
    assert b"\r\n" in raw
    # No bare-LF line endings.
    assert raw.count(b"\n") == raw.count(b"\r")


def test_save_config_no_trailing_newline(tmp_path):
    path = tmp_path / "config.json"
    save_config(str(path), {"layers": {}})

    raw = path.read_bytes()
    assert not raw.endswith(b"\n")
    assert raw.endswith(b"}")


def test_save_config_noop_roundtrip_is_byte_identical_to_real_config_json(tmp_path):
    # The real config.json is the canonical byte-style; a load->save round-trip
    # must reproduce it byte-for-byte (no whitespace diff on a no-op re-run).
    orig = open("config.json", "rb").read()
    cfg = load_config("config.json")

    out_path = tmp_path / "config.json"
    save_config(str(out_path), cfg)
    assert out_path.read_bytes() == orig


def test_save_config_is_atomic_no_temp_left_behind(tmp_path):
    # After a successful write, no temp file remains in the directory.
    save_config(str(tmp_path / "config.json"), {"layers": {}})
    leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".tmp_")]
    assert leftovers == []


def test_save_config_cleans_up_temp_on_failure(tmp_path):
    # If os.replace is sabotaged to raise, the temp file must be removed and
    # no partial config.json written.
    import conflate.config as config_module

    real_replace = os.replace
    config_path = tmp_path / "config.json"

    def boom(src, dst):
        raise OSError("simulated replace failure")

    config_module.os.replace = boom
    try:
        raised = False
        try:
            save_config(str(config_path), {"layers": {}})
        except OSError:
            raised = True
        assert raised
    finally:
        config_module.os.replace = real_replace

    # No config.json written, and no temp file left behind.
    assert not config_path.exists()
    leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".tmp_")]
    assert leftovers == []


def _valid_cfg(**over):
    cfg = {
        "authoritative_url": "A/0", "captured_url": "C/0",
        "match_threshold_m": 10.0, "field_map": {}, "copy_attachments": True,
    }
    cfg.update(over)
    return cfg


class TestValidateLayerConfigThreshold:
    """match_threshold_m must be a positive finite number -- a non-positive or
    non-finite value collapses assign_matches's cost matrix (k=0 -> nothing
    matches, k=inf -> everything matches) and silently accepts arbitrary
    matches. validate_layer_config must reject a hand-edited bad value, not
    just the cli-controlled --default-threshold-m / calibration outputs.
    """

    def test_positive_threshold_passes(self):
        validate_layer_config(_valid_cfg(match_threshold_m=0.5))  # no raise

    @pytest.mark.parametrize(
        "bad", [0, -1, -0.01, float("nan"), float("inf"), float("-inf"), "10", None]
    )
    def test_non_positive_or_non_finite_threshold_rejected(self, bad):
        with pytest.raises(ValueError, match="match_threshold_m"):
            validate_layer_config(_valid_cfg(match_threshold_m=bad))