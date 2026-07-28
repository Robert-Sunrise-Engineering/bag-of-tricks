"""Tests for conflate.gis_client.validate_geometry_type.

Matching (geodesic_distance on a feature's x/y) only makes sense for point
features; a line/polygon layer has no single x/y and would otherwise
silently produce None lon/lat deep in cli._simplify_feature, crashing later
with an opaque pyproj error. This checks the layer's geometry type fails
loudly and immediately instead.
"""

from conflate.gis_client import validate_geometry_type


class FakeLayer:
    def __init__(self, geometry_type):
        self.properties = {"geometryType": geometry_type}


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
