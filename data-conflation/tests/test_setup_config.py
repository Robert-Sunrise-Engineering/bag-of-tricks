"""Tests for setup_config.py — Phase 1 interactive config creator."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
CONFIG_LOCAL_PATH = SCRIPT_DIR / "config.local.json"


def make_mock_layer(name="Test Layer", count=42, fields=None, layer_type="Feature Layer"):
    """Create a mock arcgis.features.FeatureLayer object with correct attribute shape."""
    layer = MagicMock()
    layer.properties.name = name
    layer.properties.type = layer_type
    layer.properties.fields = [{"name": f} for f in (fields or ["SHAPE_Length", "OBJECTID"])]
    layer.query = MagicMock(return_value=count)
    return layer


@pytest.fixture(autouse=True)
def cleanup_config():
    """Remove config.local.json before and after each test."""
    if CONFIG_LOCAL_PATH.exists():
        CONFIG_LOCAL_PATH.unlink()
    yield
    if CONFIG_LOCAL_PATH.exists():
        CONFIG_LOCAL_PATH.unlink()


@pytest.fixture(autouse=True)
def reload_setup_config():
    """Force reload of setup_config module before each test to ensure fresh patches."""
    yield
    # Clean module cache after each test
    sys.modules.pop("setup_config", None)


@pytest.fixture
def gis_instance():
    """Provide a properly configured mock GIS instance."""
    gis = MagicMock()
    gis.properties.user.username = "testuser"
    return gis


class TestOverwriteProtection:
    """Test config.local.json overwrite protection."""

    def test_overwrite_defaults_no(self, gis_instance):
        """Existing config + bare Enter -> exits without writing."""
        CONFIG_LOCAL_PATH.write_text('{"agol": {}}')

        with patch("builtins.input", side_effect=[""]), \
             patch("getpass.getpass", return_value="pass"), \
             patch("arcgis.gis.GIS", return_value=gis_instance):
            with pytest.raises(SystemExit) as exc:
                from setup_config import main
                main()

        assert exc.value.code == 0
        content = json.loads(CONFIG_LOCAL_PATH.read_text())
        assert content == {"agol": {}}

    def test_overwrite_accepts_yes(self, gis_instance):
        """Existing config + 'y' -> overwrites."""
        CONFIG_LOCAL_PATH.write_text('{"agol": {}}')

        captured_layer = make_mock_layer("Captured", 100, ["FieldA"])
        auth_layer = make_mock_layer("Auth", 200, ["FieldB"])

        with patch("builtins.input", side_effect=["y", "user", "https://example.com/services/Captured/FeatureServer/0", "https://example.com/services/Auth/FeatureServer/0"]), \
             patch("getpass.getpass", return_value="pass"), \
             patch("arcgis.gis.GIS", return_value=gis_instance), \
             patch("arcgis.features.FeatureLayer", side_effect=[captured_layer, auth_layer]):
            from setup_config import main
            main()

        assert CONFIG_LOCAL_PATH.exists()
        content = json.loads(CONFIG_LOCAL_PATH.read_text())
        assert "agol" in content
        assert content["agol"]["username"] == "user"


class TestAuthentication:
    """Test AGOL authentication error handling."""

    def test_auth_failure_exits_1(self):
        """Bad credentials -> error message + exit 1."""
        with patch("builtins.input", side_effect=["user"]), \
             patch("getpass.getpass", return_value="badpass"), \
             patch("arcgis.gis.GIS", side_effect=Exception("Invalid credentials")):
            with pytest.raises(SystemExit) as exc:
                from setup_config import main
                main()

        assert exc.value.code == 1


class TestURLValidation:
    """Test URL input and validation logic."""

    def test_empty_url_rejected(self, gis_instance):
        """Empty URL -> re-prompts until non-empty."""
        captured_layer = make_mock_layer("Captured", 100, ["FieldA"])
        auth_layer = make_mock_layer("Auth", 200, ["FieldB"])

        with patch("builtins.input", side_effect=["", "", "https://example.com/services/Captured/FeatureServer/0", "https://example.com/services/Auth/FeatureServer/0"]), \
             patch("getpass.getpass", return_value="pass"), \
             patch("arcgis.gis.GIS", return_value=gis_instance), \
             patch("arcgis.features.FeatureLayer", side_effect=[captured_layer, auth_layer]):
            from setup_config import main
            main()

        assert CONFIG_LOCAL_PATH.exists()

    def test_both_urls_invalid_exits_1(self, gis_instance):
        """Both fail validation -> exit 1."""
        # Flow: username, url1, url2, retry-y, new-url1, retry-y, new-url1-2, retry-n, retry-y, new-url2, retry-y, new-url2-2, retry-n
        with patch("builtins.input", side_effect=["user", "url1", "url2", "y", "url1-retry", "y", "url1-retry2", "n", "y", "url2-retry", "y", "url2-retry2", "n"]), \
             patch("getpass.getpass", return_value="pass"), \
             patch("arcgis.gis.GIS", return_value=gis_instance), \
             patch("arcgis.features.FeatureLayer", side_effect=Exception("Invalid URL")):
            with pytest.raises(SystemExit) as exc:
                from setup_config import main
                main()

        assert exc.value.code == 1

    def test_one_url_invalid_exits_1(self, gis_instance):
        """One fails, one passes -> exit 1."""
        captured_layer = make_mock_layer("Captured", 100, ["FieldA"])

        with patch("builtins.input", side_effect=["user", "https://example.com/services/Captured/FeatureServer/0", "https://example.com/services/Auth/FeatureServer/0", "y", "https://example.com/services/Auth/FeatureServer/0", "n"]), \
             patch("getpass.getpass", return_value="pass"), \
             patch("arcgis.gis.GIS", return_value=gis_instance), \
             patch("arcgis.features.FeatureLayer", side_effect=[captured_layer, Exception("Invalid URL"), Exception("Invalid URL")]):
            with pytest.raises(SystemExit) as exc:
                from setup_config import main
                main()

        assert exc.value.code == 1

    def test_wrong_layer_type_rejected(self, gis_instance):
        """Non-Feature Layer type -> rejected with retry."""
        wrong_layer = make_mock_layer("Wrong Layer", layer_type="Table")
        captured_layer = make_mock_layer("Captured", 100, ["FieldA"])
        auth_layer = make_mock_layer("Auth", 200, ["FieldB"])

        with patch("builtins.input", side_effect=["user", "https://example.com/services/Captured/FeatureServer/0", "https://example.com/services/Auth/FeatureServer/0", "y", "https://example.com/services/Auth/FeatureServer/0"]), \
             patch("getpass.getpass", return_value="pass"), \
             patch("arcgis.gis.GIS", return_value=gis_instance), \
             patch("arcgis.features.FeatureLayer", side_effect=[captured_layer, wrong_layer, auth_layer]):
            from setup_config import main
            main()

        assert CONFIG_LOCAL_PATH.exists()


class TestConfigWriting:
    """Test that config.local.json is written correctly."""

    def test_both_valid_writes_config(self, gis_instance):
        """Both layers valid -> config.local.json written."""
        captured_layer = make_mock_layer("Captured", 100, ["FieldA"])
        auth_layer = make_mock_layer("Auth", 200, ["FieldB"])

        with patch("builtins.input", side_effect=["user", "https://example.com/services/Captured/FeatureServer/0", "https://example.com/services/Auth/FeatureServer/0"]), \
             patch("getpass.getpass", return_value="pass"), \
             patch("arcgis.gis.GIS", return_value=gis_instance), \
             patch("arcgis.features.FeatureLayer", side_effect=[captured_layer, auth_layer]):
            from setup_config import main
            main()

        assert CONFIG_LOCAL_PATH.exists()

    def test_config_structure(self, gis_instance):
        """Written JSON has all 4 required keys with correct types."""
        captured_layer = make_mock_layer("Captured", 100, ["FieldA"])
        auth_layer = make_mock_layer("Auth", 200, ["FieldB"])

        with patch("builtins.input", side_effect=["user", "https://example.com/services/Captured/FeatureServer/0", "https://example.com/services/Auth/FeatureServer/0"]), \
             patch("getpass.getpass", return_value="testpass123"), \
             patch("arcgis.gis.GIS", return_value=gis_instance), \
             patch("arcgis.features.FeatureLayer", side_effect=[captured_layer, auth_layer]):
            from setup_config import main
            main()

        content = json.loads(CONFIG_LOCAL_PATH.read_text())

        assert "agol" in content
        assert "username" in content["agol"]
        assert "password" in content["agol"]
        assert content["agol"]["username"] == "user"
        assert content["agol"]["password"] == "testpass123"
        assert "captured_layer_url" in content
        assert "auth_layer_url" in content


class TestLayerInfoDisplay:
    """Test that layer information is displayed correctly."""

    def test_layer_info_displayed(self, gis_instance, capsys):
        """Both valid -> printed output contains name, count, fields."""
        captured_layer = make_mock_layer("My Captured Layer", 42, ["Shape", "ID", "Name"])
        auth_layer = make_mock_layer("My Auth Layer", 107, ["Shape", "ID", "Value"])

        with patch("builtins.input", side_effect=["user", "https://example.com/services/My Captured Layer/FeatureServer/0", "https://example.com/services/My Auth Layer/FeatureServer/0"]), \
             patch("getpass.getpass", return_value="pass"), \
             patch("arcgis.gis.GIS", return_value=gis_instance), \
             patch("arcgis.features.FeatureLayer", side_effect=[captured_layer, auth_layer]):
            from setup_config import main
            main()

        captured = capsys.readouterr()
        output = captured.out

        assert "My Captured Layer" in output
        assert "My Auth Layer" in output
        assert "42" in output
        assert "107" in output
        assert "Shape" in output
        assert "ID" in output
        assert "Name" in output
        assert "Value" in output
