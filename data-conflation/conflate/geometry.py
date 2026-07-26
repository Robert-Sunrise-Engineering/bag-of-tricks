"""Geometry utilities for coordinate operations and transformations."""

from pyproj import Geod, Transformer


def geodesic_distance(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """
    Calculate the geodesic (great-circle, ellipsoidal) distance between two points.

    Args:
        lon1: Longitude of the first point (in degrees).
        lat1: Latitude of the first point (in degrees).
        lon2: Longitude of the second point (in degrees).
        lat2: Latitude of the second point (in degrees).

    Returns:
        The geodesic distance in meters between the two points.
    """
    geod = Geod(ellps="WGS84")
    _, _, distance = geod.inv(lon1, lat1, lon2, lat2)
    return distance


def reproject_point(x: float, y: float, from_wkid: int, to_wkid: int) -> tuple:
    """
    Reproject a point from one coordinate reference system to another.

    Args:
        x: X coordinate (longitude or easting depending on CRS).
        y: Y coordinate (latitude or northing depending on CRS).
        from_wkid: Source EPSG/WKID code (e.g., 4326 for WGS84).
        to_wkid: Destination EPSG/WKID code (e.g., 3857 for Web Mercator).

    Returns:
        A tuple (new_x, new_y) with the reprojected coordinates.
    """
    transformer = Transformer.from_crs(from_wkid, to_wkid, always_xy=True)
    new_x, new_y = transformer.transform(x, y)
    return (new_x, new_y)
