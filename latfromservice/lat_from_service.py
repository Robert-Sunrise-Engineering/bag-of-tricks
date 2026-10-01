# -*- coding: utf-8 -*-
"""
lat_from_service.py - Create lateral lines from service points.

For each service point, finds the nearest main (line feature class) and inserts
a straight lateral line into an existing line feature class, running from the
point to its nearest point on the main (perpendicular to the main segment
whenever the perpendicular foot falls inside a segment).

Points with no main within the max search distance - and points that sit on a
main (degenerate zero-length laterals) - are skipped and reported, both in the
geoprocessing messages and in a timestamped text report next to this script.

Parameters (in order, script tool or command line):
    1. Service points      - point feature class/layer
    2. Mains               - line feature class/layer (never modified)
    3. Laterals            - EXISTING line feature class the laterals are appended into
                             (only SHAPE is written; other fields are left null)
    4. Max search distance - linear unit, e.g. "150 Feet" (a bare number is
                             interpreted in the spatial reference's linear units)

Geometry is built in the laterals' spatial reference; the point and line inputs
are projected into it on temporary copies in the memory workspace when their
spatial reference differs. The laterals' spatial reference must be projected.

Run:
    ArcGIS Pro script tool (add script, four parameters), or
    propy lat_from_service.py <points> <mains> <laterals> <max_search_dist>
Self-test (synthetic data, no inputs):
    propy lat_from_service.py --selftest
"""

import os
import sys
from datetime import datetime

import arcpy

_MEM_WS = "memory"
_TMP_PTS = os.path.join(_MEM_WS, "lat_pts_tmp")
_TMP_PTS_PROJ = os.path.join(_MEM_WS, "lat_pts_proj")
_TMP_MAINS = os.path.join(_MEM_WS, "lat_mains_proj")
# Distances at or below this (in spatial-reference units) mean the point sits on a main.
ON_MAIN_TOL = 1e-6
_REPORT_EVERY = 250


def _sr_key(sr):
    """Cheap identity for comparing spatial references."""
    return (getattr(sr, "factoryCode", 0), sr.name)


def _prep_points(points_fc, target_sr):
    """Return a points path safe for Near: always a copy (Near adds fields), projected if needed."""
    desc = arcpy.Describe(points_fc)
    if desc.shapeType != "Point":
        raise arcpy.ExecuteError(
            "Service points input must be a point feature class; got %s." % desc.shapeType)
    if _sr_key(desc.spatialReference) != _sr_key(target_sr):
        arcpy.management.Project(points_fc, _TMP_PTS_PROJ, target_sr)
        return _TMP_PTS_PROJ
    arcpy.management.CopyFeatures(points_fc, _TMP_PTS)
    return _TMP_PTS


def _prep_mains(mains_fc, target_sr):
    """Return a mains path in target_sr; used read-only, so no copy unless a projection is needed."""
    desc = arcpy.Describe(mains_fc)
    if desc.shapeType != "Polyline":
        raise arcpy.ExecuteError(
            "Mains input must be a line feature class; got %s." % desc.shapeType)
    if _sr_key(desc.spatialReference) == _sr_key(target_sr):
        return mains_fc
    arcpy.management.Project(mains_fc, _TMP_MAINS, target_sr)
    return _TMP_MAINS


def _polyline(x1, y1, x2, y2, sr):
    array = arcpy.Array([arcpy.Point(x1, y1), arcpy.Point(x2, y2)])
    return arcpy.Polyline(array, sr)


def generate_laterals(points_fc, mains_fc, laterals_fc, max_search_dist):
    """Find each point's nearest main and append a lateral line to the laterals target.

    Returns (inserted, too_far_oids, on_main_oids, bad_geom_oids).
    """
    target_sr = arcpy.Describe(laterals_fc).spatialReference
    if target_sr.type != "Projected":
        raise arcpy.ExecuteError(
            "The laterals feature class must use a projected coordinate system "
            "(got %s). Project the data and run again - perpendicular construction "
            "is not valid in geographic coordinates." % target_sr.name)
    if arcpy.Describe(laterals_fc).shapeType != "Polyline":
        raise arcpy.ExecuteError("The laterals target must be a line feature class.")

    pts = _prep_points(points_fc, target_sr)
    mains = _prep_mains(mains_fc, target_sr)

    arcpy.analysis.Near(pts, mains, max_search_dist, "LOCATION")

    fields = ["OID@", "SHAPE@", "NEAR_FID", "NEAR_DIST", "NEAR_X", "NEAR_Y"]
    inserted = 0
    too_far = []
    on_main = []
    bad_geom = []
    total = int(arcpy.management.GetCount(pts)[0])
    arcpy.SetProgressor("default", "Building laterals for %d service points..." % total)

    with arcpy.da.SearchCursor(pts, fields) as sc, \
            arcpy.da.InsertCursor(laterals_fc, ["SHAPE@"]) as ic:
        for i, (oid, geom, near_fid, near_dist, near_x, near_y) in enumerate(sc, start=1):
            if i % _REPORT_EVERY == 0:
                arcpy.AddMessage("  %d of %d points processed (%d laterals inserted)"
                                 % (i, total, inserted))
            if geom is None:
                bad_geom.append(oid)
                continue
            if near_fid is None or near_fid == -1:
                too_far.append(oid)
                continue
            if near_dist is not None and near_dist <= ON_MAIN_TOL:
                on_main.append(oid)
                continue
            pt = geom.firstPoint
            if abs(pt.X - near_x) <= ON_MAIN_TOL and abs(pt.Y - near_y) <= ON_MAIN_TOL:
                on_main.append(oid)
                continue
            ic.insertRow([_polyline(pt.X, pt.Y, near_x, near_y, target_sr)])
            inserted += 1

    arcpy.ResetProgressor()
    return inserted, too_far, on_main, bad_geom


def _cleanup():
    for tmp in (_TMP_PTS, _TMP_PTS_PROJ, _TMP_MAINS):
        if arcpy.Exists(tmp):
            arcpy.management.Delete(tmp)


def _write_report(params, inserted, too_far, on_main, bad_geom):
    """Write a timestamped text report of this run; returns the path, or None if it failed."""
    try:
        report_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "lateral_report_%s.txt" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        with open(report_path, "w") as fh:
            fh.write("lat_from_service report %s\n" % datetime.now().isoformat())
            fh.write("points=%s\nmains=%s\nlaterals=%s\nmax_search_dist=%s\n"
                     % params)
            fh.write("inserted=%d\n" % inserted)
            fh.write("skipped too_far (%d): %s\n" % (len(too_far), too_far))
            fh.write("skipped on_main (%d): %s\n" % (len(on_main), on_main))
            fh.write("skipped bad_geom (%d): %s\n" % (len(bad_geom), bad_geom))
        return report_path
    except Exception:
        return None


def _report_to_gp(inserted, too_far, on_main, bad_geom, report_path):
    arcpy.AddMessage("Laterals inserted: %d" % inserted)
    if too_far:
        arcpy.AddWarning(
            "Skipped %d point(s) with no main within the max search distance (OID%s): %s"
            % (len(too_far), "" if len(too_far) == 1 else "s", too_far))
    if on_main:
        arcpy.AddWarning(
            "Skipped %d point(s) located on a main - zero-length lateral (OID%s): %s"
            % (len(on_main), "" if len(on_main) == 1 else "s", on_main))
    if bad_geom:
        arcpy.AddWarning(
            "Skipped %d point(s) with null geometry (OID%s): %s"
            % (len(bad_geom), "" if len(bad_geom) == 1 else "s", bad_geom))
    if report_path:
        arcpy.AddMessage("Report written to: %s" % report_path)


def _selftest():
    """Synthetic end-to-end check against a scratch memory geodatabase."""
    sr = arcpy.SpatialReference(26911)  # NAD83 / UTM zone 11N, meters

    def make_fc(name, geom_type):
        path = os.path.join(_MEM_WS, name)
        if arcpy.Exists(path):
            arcpy.management.Delete(path)
        arcpy.management.CreateFeatureclass(_MEM_WS, name, geom_type,
                                            None, None, None, sr)
        return path
    try:
        mains = make_fc("st_mains", "Polyline")
        points = make_fc("st_points", "Point")
        laterals = make_fc("st_laterals", "Polyline")

        with arcpy.da.InsertCursor(mains, ["SHAPE@"]) as ic:
            # Main A: straight line along y = 0
            ic.insertRow([arcpy.Polyline(
                arcpy.Array([arcpy.Point(0, 0), arcpy.Point(1000, 0)]), sr)])
            # Main B: straight line along x = 1000
            ic.insertRow([arcpy.Polyline(
                arcpy.Array([arcpy.Point(1000, 0), arcpy.Point(1000, 1000)]), sr)])

        with arcpy.da.InsertCursor(points, ["SHAPE@"]) as ic:
            ic.insertRow([arcpy.Point(500, 100)])    # interior foot on A -> lateral
            ic.insertRow([arcpy.Point(1500, 100)])   # interior foot on B -> lateral
            ic.insertRow([arcpy.Point(500, 5000)])   # far from everything -> skip
            ic.insertRow([arcpy.Point(200, 0)])      # sits on A -> skip

        inserted, too_far, on_main, bad_geom = generate_laterals(
            points, mains, laterals, "2000 Meters")

        assert inserted == 2, "expected 2 laterals, got %d" % inserted
        assert too_far == [3], "too_far should be [OID 3], got %s" % too_far
        assert on_main == [4], "on_main should be [OID 4], got %s" % on_main
        assert bad_geom == [], "bad_geom should be empty, got %s" % bad_geom

        actual = []
        with arcpy.da.SearchCursor(laterals, ["SHAPE@"]) as sc:
            for (line,) in sc:
                pts = [(p.X, p.Y) for p in line.getPart(0)]
                actual.append(pts)
        expected = [[(500, 100), (500, 0)], [(1500, 100), (1000, 100)]]
        for got, want in zip(actual, expected):
            for (gx, gy), (wx, wy) in zip(got, want):
                assert abs(gx - wx) < 1e-9 and abs(gy - wy) < 1e-9, \
                    "lateral endpoint mismatch: %s vs %s" % (got, want)

        # Perpendicularity: lateral direction vs its main's direction must have ~zero dot product
        later1 = actual[0]
        dot = ((later1[1][0] - later1[0][0]) * 1.0
               + (later1[1][1] - later1[0][1]) * 0.0)  # main A direction = (1, 0)
        assert abs(dot) < 1e-9, "lateral 1 not perpendicular to main A (dot=%s)" % dot
        later2 = actual[1]
        dot = ((later2[1][0] - later2[0][0]) * 0.0  # main B direction = (0, 1)
               + (later2[1][1] - later2[0][1]) * 1.0)
        assert abs(dot) < 1e-9, "lateral 2 not perpendicular to main B (dot=%s)" % dot
        for pts_ in actual:
            assert len(pts_) == 2, "laterals must be 2-point lines, got %s" % pts_

        print("SELFTEST PASSED (%d laterals; skips: %s)" % (inserted, (too_far, on_main)))
    finally:
        _cleanup()


def _get_params(n=4):
    """Parameter values from the script tool, or from sys.argv when run standalone."""
    values = []
    try:
        values = [arcpy.GetParameterAsText(i) for i in range(n)]
        if all(values):
            return values
    except Exception:
        pass
    argv = sys.argv[1:]
    if len(argv) >= n:
        return argv[:n]
    if len(values) == n:
        return values
    raise arcpy.ExecuteError(
        "Expected %d parameters (service points, mains, laterals target, "
        "max search distance), got fewer." % n)


def main():
    if "--selftest" in sys.argv:
        _selftest()
        return
    points_fc, mains_fc, laterals_fc, max_search_dist = _get_params()
    try:
        inserted, too_far, on_main, bad_geom = generate_laterals(
            points_fc, mains_fc, laterals_fc, max_search_dist)
        params = (points_fc, mains_fc, laterals_fc, max_search_dist)
        report_path = _write_report(params, inserted, too_far, on_main, bad_geom)
        _report_to_gp(inserted, too_far, on_main, bad_geom, report_path)
    finally:
        _cleanup()


if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except arcpy.ExecuteError as err:
        arcpy.AddError(str(err))
        sys.exit(1)
    except Exception as err:
        arcpy.AddError("Unexpected failure: %r" % err)
        sys.exit(1)