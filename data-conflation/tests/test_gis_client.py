"""Tests for conflate.gis_client.

Covers two things:

* ``validate_geometry_type`` — matching (geodesic_distance on a feature's
  x/y) only makes sense for point features; a line/polygon layer has no
  single x/y and would otherwise silently produce None lon/lat deep in
  features.simplify_feature, crashing later with an opaque pyproj error.
  This checks the layer's geometry type fails loudly and immediately instead.
* ``list_service_sublayers`` — enumerates a feature-service root URL's
  spatial sublayers from the root-service JSON, excluding tables, building
  recognizable sublayer URLs, and rejecting a sublayer URL passed by mistake.
  Tested against a fake FeatureLayerCollection (no real network).
"""

import logging

from conflate.gis_client import validate_geometry_type, list_service_sublayers
import conflate.gis_client as gis_client


class FakeLayer:
    def __init__(self, geometry_type):
        self.properties = {"geometryType": geometry_type}


class _FakeFLC:
    """Stand-in for arcgis.features.FeatureLayerCollection covering only what
    list_service_sublayers touches: .properties as a dict with 'layers'/'tables'.
    """

    def __init__(self, properties):
        self.properties = properties


def _patch_flc(monkeypatch, properties):
    """Patch FeatureLayerCollection so list_service_sublayers sees `properties`."""
    monkeypatch.setattr(
        gis_client, "FeatureLayerCollection", lambda url, gis=None: _FakeFLC(properties)
    )


def test_point_layer_passes():
    layer = FakeLayer("esriGeometryPoint")
    validate_geometry_type(layer)  # should not raise


def test_polygon_layer_raises():
    layer = FakeLayer("esriGeometryPolygon")
    try:
        validate_geometry_type(layer)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "esriGeometryPolygon" in str(e)


def test_polyline_layer_raises():
    layer = FakeLayer("esriGeometryPolyline")
    try:
        validate_geometry_type(layer)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "esriGeometryPolyline" in str(e)


def test_missing_geometry_type_raises():
    layer = FakeLayer("")
    try:
        validate_geometry_type(layer)
        assert False, "expected ValueError"
    except ValueError:
        pass


SERVICE_URL = "https://example.com/arcgis/rest/services/Water/FeatureServer"


def test_list_service_sublayers_returns_spatial_layers_with_urls(monkeypatch):
    properties = {
        "layers": [
            {"id": 0, "name": "water_hydrants", "geometryType": "esriGeometryPoint"},
            {"id": 4, "name": "water_network_structures", "geometryType": "esriGeometryPolygon"},
        ],
        "tables": [{"id": 1, "name": "water_maintenance_log"}],
    }
    _patch_flc(monkeypatch, properties)

    result = list_service_sublayers("fake-gis", SERVICE_URL)
    assert [s["name"] for s in result] == ["water_hydrants", "water_network_structures"]
    # URLs are built from the caller's root URL + sublayer id, not a resolved
    # SDK URL, so generated config values stay recognizable.
    assert result[0]["url"] == SERVICE_URL + "/0"
    assert result[1]["url"] == SERVICE_URL + "/4"
    assert result[0]["geometry_type"] == "esriGeometryPoint"


def test_list_service_sublayers_excludes_tables(monkeypatch):
    properties = {
        "layers": [{"id": 0, "name": "water_hydrants", "geometryType": "esriGeometryPoint"}],
        "tables": [
            {"id": 1, "name": "water_maintenance_log"},
            {"id": 2, "name": "inspections"},
        ],
    }
    _patch_flc(monkeypatch, properties)

    result = list_service_sublayers("fake-gis", SERVICE_URL)
    assert [s["name"] for s in result] == ["water_hydrants"]
    assert all(s["id"] != 1 and s["id"] != 2 for s in result)


def test_list_service_sublayers_missing_geometry_type_becomes_none(monkeypatch):
    properties = {
        "layers": [{"id": 0, "name": "water_hydrants"}],  # no geometryType
        "tables": [],
    }
    _patch_flc(monkeypatch, properties)

    result = list_service_sublayers("fake-gis", SERVICE_URL)
    assert result[0]["geometry_type"] is None


def test_list_service_sublayers_skips_entry_with_no_id(monkeypatch, caplog):
    # A sublayer entry without an id can't form a valid sublayer URL -- it
    # would build '.../FeatureServer/None' and silently corrupt config.json.
    # It must be skipped (with a warning), not emitted.
    properties = {
        "layers": [
            {"id": 0, "name": "water_hydrants", "geometryType": "esriGeometryPoint"},
            {"name": "orphan_no_id", "geometryType": "esriGeometryPoint"},
        ],
        "tables": [],
    }
    _patch_flc(monkeypatch, properties)

    with caplog.at_level(logging.WARNING, logger="conflate.gis_client"):
        result = list_service_sublayers("fake-gis", SERVICE_URL)
    assert [s["name"] for s in result] == ["water_hydrants"]
    assert all("None" not in s["url"] for s in result)
    assert "orphan_no_id" in caplog.text


def test_list_service_sublayers_skips_entry_with_no_name(monkeypatch, caplog):
    # An unnamed sublayer can't be matched by name (match_sublayers does
    # name.lower()) -- skip it (with a warning) rather than let None propagate
    # and AttributeError-abort the whole --auto-configure run.
    properties = {
        "layers": [
            {"id": 0, "name": "water_hydrants", "geometryType": "esriGeometryPoint"},
            {"id": 5, "geometryType": "esriGeometryPoint"},  # no name
        ],
        "tables": [],
    }
    _patch_flc(monkeypatch, properties)

    with caplog.at_level(logging.WARNING, logger="conflate.gis_client"):
        result = list_service_sublayers("fake-gis", SERVICE_URL)
    assert [s["name"] for s in result] == ["water_hydrants"]
    assert "5" in caplog.text


def test_list_service_sublayers_handles_attribute_style_entries(monkeypatch):
    # Some SDK/server versions wrap each summary entry in an attribute-access
    # object rather than a plain dict. _prop must handle both.
    class _Entry:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    properties = {
        "layers": [
            _Entry(id=0, name="water_hydrants", geometryType="esriGeometryPoint"),
        ],
        "tables": [],
    }
    _patch_flc(monkeypatch, properties)

    result = list_service_sublayers("fake-gis", SERVICE_URL)
    assert result[0]["name"] == "water_hydrants"
    assert result[0]["id"] == 0
    assert result[0]["geometry_type"] == "esriGeometryPoint"


def test_list_service_sublayers_sublayer_url_rejected(monkeypatch):
    _patch_flc(monkeypatch, {"layers": [], "tables": []})
    sublayer_url = SERVICE_URL + "/0"
    try:
        list_service_sublayers("fake-gis", sublayer_url)
        assert False, "expected ValueError for sublayer URL"
    except ValueError as e:
        assert "sublayer URL" in str(e)


def test_list_service_sublayers_strips_trailing_slash_in_url(monkeypatch):
    properties = {
        "layers": [{"id": 0, "name": "water_hydrants", "geometryType": "esriGeometryPoint"}],
        "tables": [],
    }
    _patch_flc(monkeypatch, properties)

    result = list_service_sublayers("fake-gis", SERVICE_URL + "/")
    assert result[0]["url"] == SERVICE_URL + "/0"
