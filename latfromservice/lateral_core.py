# -*- coding: utf-8 -*-
"""
lateral_core.py - Canonical engine for building water service laterals.

For each service point, finds the nearest main and appends a straight lateral to
an existing line feature class. Every connection is made topological: a vertex
is inserted into the main at the connection point (unless one is already within
the connection tolerance), and the lateral's far endpoint is placed on exactly
that vertex. Points that already have a lateral, sit on a main, have no main
within the search distance, or share a coordinate with another service point are
classified and reported instead of duplicated.

This module is the single implementation shared by:
    lat_from_service.py   - command-line / ArcGIS Pro script tool (thin wrapper)
    LateralTools.pyt      - ArcGIS Pro Python toolbox (thin wrapper)

Keeping the logic here means the two entry points cannot drift apart.

All analysis runs before any write. The write phase (main vertex insertion +
lateral insertion) can be wrapped in an arcpy.da.Editor transaction, so a mid-run
failure rolls back instead of leaving mains half-split. ArcGIS Pro owns editing
in the toolbox path, which therefore runs with transactional=False.

Main types and units
--------------------
- Service points: Point.  Mains and the laterals target: Polyline.
- The laterals target's projected coordinate system is the working SR; points
  and mains are projected into it on temporary memory copies.
- Distances are linear units ("100 Feet", "1 Foot") or, when written as a bare
  number, the working SR's linear units. Mains and laterals may use different
  SRs - main vertices are always written in the mains' own SR.

Main vertices are rebuilt from the vertices arcpy returns from getPart(), so
curved segments (arcs, beziers) are flattened to points when a main gains a
vertex. The project data contains no curves; on data that does, expect the
affected features to be densified. Z and M values are carried across and
interpolated at inserted vertices.

Run the self-test:
    propy lat_from_service.py --selftest
"""

import math
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime

import arcpy

_MEM_WS = "memory"
_TMP_PREFIX = "lat_"
_REPORT_EVERY = 500
_PERCENTILE_STOPS = (50, 80, 95, 97, 99)

# Meters per named linear unit, for converting parameter text to SR units.
_UNIT_METERS = {
    "inch": 0.0254, "inches": 0.0254, "in": 0.0254,
    "foot": 0.3048, "feet": 0.3048, "ft": 0.3048,
    "us survey foot": 1200.0 / 3937.0, "us survey feet": 1200.0 / 3937.0,
    "us foot": 1200.0 / 3937.0, "us feet": 1200.0 / 3937.0,
    "yard": 0.9144, "yards": 0.9144, "yd": 0.9144,
    "mile": 1609.344, "miles": 1609.344, "mi": 1609.344,
    "nautical mile": 1852.0, "nautical miles": 1852.0,
    "millimeter": 0.001, "millimeters": 0.001, "mm": 0.001,
    "centimeter": 0.01, "centimeters": 0.01, "cm": 0.01,
    "decimeter": 0.1, "decimeters": 0.1,
    "meter": 1.0, "meters": 1.0, "m": 1.0,
    "kilometer": 1000.0, "kilometers": 1000.0, "km": 1000.0,
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _sr_key(sr):
    """Cheap identity for comparing spatial references."""
    if sr is None:
        return (None, None)
    return (getattr(sr, "factoryCode", None), getattr(sr, "name", None))


def _same_sr(a, b):
    return _sr_key(a) == _sr_key(b)


def _project_xy(x, y, from_sr, to_sr):
    """Transform a bare coordinate pair between spatial references."""
    if _same_sr(from_sr, to_sr):
        return (x, y)
    pt = arcpy.PointGeometry(arcpy.Point(x, y), from_sr).projectAs(to_sr).firstPoint
    return (pt.X, pt.Y)


def _linear_to_sr_units(text, sr):
    """Convert a linear-unit string ("100 Feet", "1 Foot", or bare "100") to SR units.

    A bare number is taken as already being in the spatial reference's linear
    units, which matches how ArcGIS tools read a unit parameter.
    """
    raw = str(text).strip()
    match = re.match(r"^([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*)$", raw)
    if not match:
        raise arcpy.ExecuteError(
            "Could not read the linear distance %r. Use a value such as "
            "'100 Feet' or '30 Meters'." % text)
    value = float(match.group(1))
    unit = re.sub(r"\s+", " ", match.group(2).strip().lower())
    if not unit:
        return value
    if unit not in _UNIT_METERS:
        raise arcpy.ExecuteError(
            "Unrecognized linear unit %r in %r. Use Feet, US Survey Feet, Yards, "
            "Miles, Meters, Kilometers, Centimeters, Millimeters or Inches."
            % (unit, text))
    meters_per_unit = getattr(sr, "metersPerUnit", None) or 1.0
    return value * _UNIT_METERS[unit] / meters_per_unit


def _percentiles(values, stops=_PERCENTILE_STOPS):
    """Nearest-rank percentiles of a list of numbers, sorted ascending."""
    if not values:
        return {}
    ordered = sorted(values)
    count = len(ordered)
    result = {}
    for stop in stops:
        rank = int(math.ceil(stop / 100.0 * count)) - 1
        rank = max(0, min(count - 1, rank))
        result["p%d" % stop] = ordered[rank]
    return result


def _lerp(a, b, t):
    """Linear interpolation that tolerates null Z or M values."""
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return a + (b - a) * t


def _oid_text(oids, limit=250):
    """Comma-separated OID list, truncated with a count for long lists."""
    if not oids:
        return "(none)"
    shown = [str(o) for o in oids[:limit]]
    if len(oids) > limit:
        shown.append("... (%d more)" % (len(oids) - limit))
    return ", ".join(shown)


def _workspace_of(fc):
    """Containing workspace of a feature class or layer path.

    A feature class inside a feature dataset describes its path as
    ...\\file.gdb\\FeatureDataset. Editing and schema-lock checks need the
    geodatabase itself, so walk out to the .gdb or .sde workspace.
    """
    try:
        folder = os.path.dirname(arcpy.Describe(fc).catalogPath)
    except Exception:
        return ""
    current = folder
    while current:
        low = current.lower()
        if low.endswith(".gdb") or low.endswith(".sde"):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return folder


# ---------------------------------------------------------------------------
# Temporary workspace management
# ---------------------------------------------------------------------------

def _delete_temps():
    """Remove every stale memory/lat_* temporary left by an earlier run."""
    previous = arcpy.env.workspace
    try:
        arcpy.env.workspace = _MEM_WS
        for name in arcpy.ListFeatureClasses(_TMP_PREFIX + "*") or []:
            try:
                arcpy.management.Delete(name)
            except Exception:
                pass
        for name in arcpy.ListTables(_TMP_PREFIX + "*") or []:
            try:
                arcpy.management.Delete(name)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        try:
            arcpy.env.workspace = previous
        except Exception:
            pass


def _cleanup():
    _delete_temps()


def _copy_projected(source, out_name, target_sr):
    """Copy a feature class into memory/out_name, reprojected to target_sr.

    The source OBJECTID is carried in a SRC_OID field so reports can quote the
    caller's OIDs and Near results can be mapped back to them without relying on
    the projection tool preserving OIDs.
    """
    desc = arcpy.Describe(source)
    destination = os.path.join(_MEM_WS, out_name)
    arcpy.management.CreateFeatureclass(
        _MEM_WS, out_name, desc.shapeType,
        template=None,
        has_m="ENABLED" if desc.hasM else "DISABLED",
        has_z="ENABLED" if desc.hasZ else "DISABLED",
        spatial_reference=target_sr)
    arcpy.management.AddField(destination, "SRC_OID", "LONG")

    keep = _same_sr(desc.spatialReference, target_sr)
    with arcpy.da.SearchCursor(source, ["OID@", "SHAPE@"]) as reader, \
            arcpy.da.InsertCursor(destination, ["SHAPE@", "SRC_OID"]) as writer:
        for oid, geom in reader:
            if geom is None:
                writer.insertRow([None, oid])
            else:
                writer.insertRow([geom if keep else geom.projectAs(target_sr), oid])
    return destination


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(points_fc, mains_fc, laterals_fc, preview):
    """Fail early on anything that would otherwise corrupt the write phase."""
    for path, label in ((points_fc, "Service points"),
                        (mains_fc, "Mains"),
                        (laterals_fc, "Laterals target")):
        if not path or not arcpy.Exists(path):
            raise arcpy.ExecuteError("%s input does not exist: %r" % (label, path))

    points_desc = arcpy.Describe(points_fc)
    if points_desc.shapeType != "Point":
        raise arcpy.ExecuteError(
            "Service points must be a point feature class; got %s."
            % points_desc.shapeType)

    mains_desc = arcpy.Describe(mains_fc)
    if mains_desc.shapeType != "Polyline":
        raise arcpy.ExecuteError(
            "Mains must be a line feature class; got %s." % mains_desc.shapeType)

    laterals_desc = arcpy.Describe(laterals_fc)
    if laterals_desc.shapeType != "Polyline":
        raise arcpy.ExecuteError(
            "The laterals target must be a line feature class; got %s."
            % laterals_desc.shapeType)

    if laterals_desc.spatialReference.type != "Projected":
        raise arcpy.ExecuteError(
            "The laterals feature class must use a projected coordinate system "
            "(got %s). Project the data and run again - perpendicular connection "
            "construction is not valid in geographic coordinates."
            % laterals_desc.spatialReference.name)

    field_names = {f.name.upper() for f in arcpy.ListFields(laterals_fc)}
    shape_field = arcpy.Describe(laterals_fc).shapeFieldName
    if shape_field.upper() not in field_names:
        raise arcpy.ExecuteError(
            "The laterals target has no geometry field: %s" % laterals_fc)

    try:
        same_target = (laterals_desc.catalogPath.lower()
                       == mains_desc.catalogPath.lower())
    except Exception:
        same_target = False
    if same_target:
        raise arcpy.ExecuteError(
            "The mains and the laterals target are the same feature class. "
            "Append laterals into a separate line feature class.")

    if preview:
        return

    # A stale Pro edit session on either dataset would make the writes fail
    # halfway through; check the locks while nothing has been modified yet.
    for path, label in ((laterals_fc, "laterals target"), (mains_fc, "mains")):
        workspace = _workspace_of(path)
        if workspace.lower().endswith(".gdb") and not arcpy.TestSchemaLock(path):
            raise arcpy.ExecuteError(
                "Cannot acquire a schema lock on the %s (%s). Close the open "
                "edit session in ArcGIS Pro (or the other process using it) and "
                "run again." % (label, path))


# ---------------------------------------------------------------------------
# Analysis (no writes)
# ---------------------------------------------------------------------------

def _analyze(points_fc, mains_fc, laterals_fc, target_sr, maximum_sr,
             connection_tolerance, skip_connected, tolerance_sr):
    """Classify every service point and return the connection list."""
    points_tmp = _copy_projected(points_fc, "lat_pts", target_sr)
    mains_tmp = _copy_projected(mains_fc, "lat_mains", target_sr)

    # Near runs without a search radius so every point gets a real nearest-main
    # distance: the report can rank the outliers, and the percentile table is not
    # truncated at the limit (which would make the limit look self-justifying).
    # The limit itself is applied below.
    arcpy.AddMessage("Finding the nearest main for each service point...")
    arcpy.analysis.Near(points_tmp, mains_tmp, None, "LOCATION")

    main_oid_map = {}
    with arcpy.da.SearchCursor(mains_tmp, ["OID@", "SRC_OID"]) as reader:
        for copy_oid, source_oid in reader:
            main_oid_map[copy_oid] = source_oid

    connected = {}
    if skip_connected:
        arcpy.AddMessage("Checking which service points already have a lateral...")
        points_conn = _copy_projected(points_fc, "lat_pts_conn", target_sr)
        arcpy.analysis.Near(points_conn, laterals_fc, connection_tolerance,
                            "LOCATION")
        with arcpy.da.SearchCursor(
                points_conn, ["SRC_OID", "NEAR_FID", "NEAR_DIST"]) as reader:
            for source_oid, near_fid, near_dist in reader:
                if near_fid is not None and near_fid != -1:
                    connected[source_oid] = near_dist

    categories = {"build": [], "too_far": [], "already_connected": [],
                  "on_main": [], "duplicate_point": [], "bad_geom": []}
    connections = []
    too_far_distances = {}
    unserved_distances = []
    seen_starts = set()

    cursor_fields = ["SRC_OID", "SHAPE@", "NEAR_FID", "NEAR_DIST",
                     "NEAR_X", "NEAR_Y"]
    with arcpy.da.SearchCursor(points_tmp, cursor_fields) as reader:
        for source_oid, geom, near_fid, near_dist, near_x, near_y in reader:
            if geom is None:
                categories["bad_geom"].append(source_oid)
                continue
            if source_oid in connected:
                categories["already_connected"].append(source_oid)
                continue
            if near_fid is None or near_fid == -1 or near_x is None \
                    or near_y is None:
                categories["too_far"].append(source_oid)
                too_far_distances[source_oid] = (
                    near_dist if near_dist is not None else -1.0)
                continue

            distance = near_dist if near_dist is not None else 0.0
            unserved_distances.append(distance)
            if distance > maximum_sr:
                categories["too_far"].append(source_oid)
                too_far_distances[source_oid] = distance
                continue
            kind = "on_main" if distance <= tolerance_sr else "build"
            first = geom.firstPoint
            if kind == "build":
                # Two service points at the same coordinate would produce the
                # same lateral twice; the first one builds, the rest are listed.
                start = (round(first.X, 6), round(first.Y, 6))
                if start in seen_starts:
                    categories["duplicate_point"].append(source_oid)
                    continue
                seen_starts.add(start)
            categories[kind].append(source_oid)

            connections.append({
                "src_oid": source_oid,
                "main_src_oid": main_oid_map.get(near_fid),
                "px": first.X,
                "py": first.Y,
                "pz": getattr(first, "Z", None),
                "nx": near_x,
                "ny": near_y,
                "near_dist": distance,
                "kind": kind,
            })

    return {
        "categories": categories,
        "connections": connections,
        "too_far_distances": too_far_distances,
        "percentiles": _percentiles(unserved_distances),
        "percentile_count": len(unserved_distances),
        "total_points": sum(len(v) for v in categories.values()),
    }


# ---------------------------------------------------------------------------
# Vertex insertion (the topology fix)
# ---------------------------------------------------------------------------

def _rebuild_geometry(geom, inserts, tolerance, spatial_reference,
                      has_z=False, has_m=False):
    """Insert connection points into a polyline and return the rebuilt geometry.

    `inserts` is a list of (x, y) in the geometry's spatial reference.
    Returns (new_geometry_or_None, results) where results[i] is
    ("existing", x, y) when the connection snapped to a vertex already present,
    ("new", x, y) when a vertex was inserted, or (None, x, y) when the point
    could not be placed within `tolerance` of the geometry. new_geometry is None
    when nothing was inserted, so the caller can leave the feature untouched.

    Curves are flattened to points by getPart(); Z and M are interpolated at
    each inserted vertex and the part structure is preserved.
    """
    parts = []
    for index in range(geom.partCount):
        part = geom.getPart(index)
        if part is None:
            parts.append([])
            continue
        parts.append([pt for pt in part if pt is not None])

    tolerance_sq = tolerance * tolerance
    scheduled = []          # vertices already planned on this feature
    assignments = []        # (part, segment, t, x, y) or None, per insert
    results = []

    for x, y in inserts:
        # 1. Snap to an existing vertex when one is within the tolerance.
        nearest = None
        nearest_sq = None
        for points in parts:
            for vertex in points:
                dx = vertex.X - x
                dy = vertex.Y - y
                d2 = dx * dx + dy * dy
                if nearest_sq is None or d2 < nearest_sq:
                    nearest_sq = d2
                    nearest = vertex
        if nearest_sq is not None and nearest_sq <= tolerance_sq:
            results.append(("existing", nearest.X, nearest.Y))
            assignments.append(None)
            continue

        # 2. Snap to a vertex already planned from another service point.
        duplicate = None
        for sx, sy in scheduled:
            if (sx - x) ** 2 + (sy - y) ** 2 <= tolerance_sq:
                duplicate = (sx, sy)
                break
        if duplicate is not None:
            results.append(("existing", duplicate[0], duplicate[1]))
            assignments.append(None)
            continue

        # 3. Otherwise insert a vertex into the closest segment.
        best = None     # (distance_sq, part_index, segment_index, t)
        for part_index, points in enumerate(parts):
            for segment in range(len(points) - 1):
                start = points[segment]
                end = points[segment + 1]
                dx = end.X - start.X
                dy = end.Y - start.Y
                length_sq = dx * dx + dy * dy
                if length_sq <= 0:
                    t = 0.0
                else:
                    t = ((x - start.X) * dx + (y - start.Y) * dy) / length_sq
                    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                foot_x = start.X + t * dx
                foot_y = start.Y + t * dy
                d2 = (foot_x - x) ** 2 + (foot_y - y) ** 2
                if best is None or d2 < best[0]:
                    best = (d2, part_index, segment, t)

        if best is None or best[0] > tolerance_sq:
            results.append((None, x, y))
            assignments.append(None)
            continue

        _, part_index, segment, t = best
        assignments.append((part_index, segment, t, x, y))
        scheduled.append((x, y))
        results.append(("new", x, y))

    extras = defaultdict(list)      # (part, segment) -> [(t, x, y)]
    for assignment in assignments:
        if assignment is not None:
            part_index, segment, t, x, y = assignment
            extras[(part_index, segment)].append((t, x, y))

    if not extras:
        return None, results

    new_parts = []
    for part_index, points in enumerate(parts):
        if not points:
            continue
        array = arcpy.Array()
        for index, vertex in enumerate(points):
            array.add(vertex)
            if index >= len(points) - 1:
                continue
            segment_inserts = extras.get((part_index, index))
            if not segment_inserts:
                continue
            following = points[index + 1]
            for t, x, y in sorted(segment_inserts, key=lambda item: item[0]):
                z = _lerp(getattr(vertex, "Z", None),
                          getattr(following, "Z", None), t) if has_z else None
                m = _lerp(getattr(vertex, "M", None),
                          getattr(following, "M", None), t) if has_m else None
                array.add(arcpy.Point(x, y, z, m))
        new_parts.append(array)

    new_geometry = arcpy.Polyline(arcpy.Array(new_parts), spatial_reference,
                                  has_z, has_m)
    return new_geometry, results


def _apply_vertices(mains_fc, mains_sr, target_sr, connections, tolerance,
                    write=True):
    """Insert a main vertex for every connection; return resolved coordinates.

    Vertices are written to the original mains in their own spatial reference.
    When `write` is False (preview) the same resolution runs read-only, so the
    preview reports exactly what the real run would do.
    """
    same_sr = _same_sr(mains_sr, target_sr)
    if same_sr:
        tolerance_mains = tolerance
    else:
        target_meters = getattr(target_sr, "metersPerUnit", None) or 1.0
        mains_meters = getattr(mains_sr, "metersPerUnit", None) or 1.0
        tolerance_mains = tolerance * target_meters / mains_meters

    mains_desc = arcpy.Describe(mains_fc)
    has_z = bool(mains_desc.hasZ)
    has_m = bool(mains_desc.hasM)

    grouped = defaultdict(list)
    for connection in connections:
        if connection["main_src_oid"] is not None:
            grouped[connection["main_src_oid"]].append(connection)

    resolved = {}
    inserted = 0
    snapped = 0
    unresolved = []

    cursor_class = arcpy.da.UpdateCursor if write else arcpy.da.SearchCursor
    with cursor_class(mains_fc, ["OID@", "SHAPE@"]) as cursor:
        for oid, geom in cursor:
            pending = grouped.get(oid)
            if not pending:
                continue
            if geom is None:
                unresolved.extend(c["src_oid"] for c in pending)
                continue

            inserts = [_project_xy(c["nx"], c["ny"], target_sr, mains_sr)
                       for c in pending]
            new_geometry, results = _rebuild_geometry(
                geom, inserts, tolerance_mains, mains_sr, has_z, has_m)

            for connection, (kind, x, y) in zip(pending, results):
                if kind is None:
                    unresolved.append(connection["src_oid"])
                    continue
                resolved[connection["src_oid"]] = _project_xy(
                    x, y, mains_sr, target_sr)
                if kind == "new":
                    inserted += 1
                else:
                    snapped += 1

            if write and new_geometry is not None:
                cursor.updateRow([oid, new_geometry])

    return {
        "resolved": resolved,
        "inserted": inserted,
        "snapped": snapped,
        "unresolved": unresolved,
    }


# ---------------------------------------------------------------------------
# Lateral insertion
# ---------------------------------------------------------------------------

def _insert_laterals(laterals_fc, connections, resolved, target_sr):
    """Append a two-point lateral per connection; returns (inserted, degenerate)."""
    desc = arcpy.Describe(laterals_fc)
    has_z = bool(desc.hasZ)
    has_m = bool(desc.hasM)

    total = len(connections)
    inserted = 0
    degenerate = []
    arcpy.SetProgressor("step", "Inserting laterals...", 0, max(total, 1), 1)
    try:
        with arcpy.da.InsertCursor(laterals_fc, ["SHAPE@"]) as cursor:
            for index, connection in enumerate(connections, start=1):
                if index % _REPORT_EVERY == 0 or index == total:
                    arcpy.SetProgressorPosition(index)
                    arcpy.SetProgressorLabel(
                        "Inserting laterals... %d of %d (%d inserted)"
                        % (index, total, inserted))

                x1 = connection["px"]
                y1 = connection["py"]
                x2, y2 = resolved.get(connection["src_oid"],
                                      (connection["nx"], connection["ny"]))
                if (x1 - x2) ** 2 + (y1 - y2) ** 2 <= 1e-18:
                    degenerate.append(connection["src_oid"])
                    continue

                z = connection.get("pz") if has_z else None
                start = arcpy.Point(x1, y1, z, None)
                end = arcpy.Point(x2, y2, z, None)
                cursor.insertRow([arcpy.Polyline(arcpy.Array([start, end]),
                                                 target_sr, has_z, has_m)])
                inserted += 1
    finally:
        arcpy.ResetProgressor()
    return inserted, degenerate


# ---------------------------------------------------------------------------
# Transaction helper
# ---------------------------------------------------------------------------

def _run_in_edit_session(workspace, function):
    """Run `function` inside an arcpy.da.Editor transaction on `workspace`."""
    try:
        multiuser = not workspace.lower().endswith(".gdb")
        editor = arcpy.da.Editor(workspace)
        editor.startEditing(False, multiuser)
    except Exception as err:
        if workspace.lower().endswith(".gdb"):
            raise arcpy.ExecuteError(
                "Could not open a transactional edit session on %s (%r). "
                "No data were written; refusing to fall back to a "
                "non-transactional write." % (workspace, err)) from err
        arcpy.AddWarning(
            "Could not open an edit session on %s (%r); the writes will not be "
            "transactional." % (workspace, err))
        return function()

    editor.startOperation()
    finished = False
    try:
        result = function()
        editor.stopOperation()
        finished = True
        return result
    except Exception:
        try:
            editor.abortOperation()
        except Exception:
            pass
        raise
    finally:
        try:
            editor.stopEditing(finished)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _report_text(result):
    """Body of the timestamped text report."""
    lines = [
        "lateral_report %s" % datetime.now().isoformat(timespec="seconds"),
        "mode=%s" % ("preview (no geodatabase writes)" if result["preview"]
                     else "apply"),
        "points=%s" % result["inputs"]["points"],
        "mains=%s" % result["inputs"]["mains"],
        "laterals=%s" % result["inputs"]["laterals"],
        "max_search_dist=%s" % result["max_search_dist"],
        "connection_tolerance=%s" % result["connection_tolerance"],
        "skip_connected=%s" % result["skip_connected"],
        "insert_main_vertices=%s" % result["insert_main_vertices"],
        "laterals_sr=%s" % result["laterals_sr"],
        "mains_sr=%s" % result["mains_sr"],
        "distance_units=%s" % result["distance_units"],
        "total_points=%d" % result["total_points"],
        "",
        "counts:",
    ]
    for key in ("build", "too_far", "already_connected", "on_main",
                "duplicate_point", "bad_geom"):
        lines.append("  %-18s %d" % (key, result["counts"][key]))
    lines.extend([
        "",
        "laterals_inserted=%d" % result["inserted"],
        "laterals_would_insert=%d" % result["would_insert"],
        "vertices_inserted=%d" % result["vertices_inserted"],
        "vertices_would_insert=%d" % result["would_insert_vertices"],
        "vertices_snapped_to_existing=%d" % result["vertices_snapped"],
        "unresolved_connections=%d" % len(result["unresolved"]),
        "degenerate_laterals=%d" % len(result["degenerate"]),
        "",
        "distance percentiles over unserved points that have a main (n=%d) "
        "[untruncated, so the limit can be judged from the data]:"
        % result["percentile_count"],
    ])
    for key in ("p50", "p80", "p95", "p97", "p99"):
        if key in result["percentiles"]:
            lines.append("  %-4s %10.2f %s" % (key, result["percentiles"][key],
                                               result["distance_units"]))
    lines.append("")
    lines.append("too_far (%d), distance to nearest main [max %s; -1 = no main "
                 "found]:" % (len(result["too_far"]), result["max_search_dist"]))
    for oid in result["too_far"]:
        lines.append("  %d\t%.4f" % (oid, result["too_far_distances"].get(oid, -1.0)))
    for key, title in (("already_connected", "already_connected"),
                       ("on_main", "on_main (vertex only, no lateral)"),
                       ("duplicate_point",
                        "duplicate_point (same coordinate as an earlier service "
                        "point; one lateral inserted)"),
                       ("bad_geom", "bad_geom")):
        oids = result[key]
        lines.append("")
        lines.append("%s (%d):" % (title, len(oids)))
        for oid in oids:
            lines.append("  %d" % oid)
    if result["unresolved"]:
        lines.append("")
        lines.append("unresolved connections (no main vertex placed) (%d):"
                     % len(result["unresolved"]))
        for oid in result["unresolved"]:
            lines.append("  %d" % oid)
    lines.append("")
    return "\n".join(lines)


def _write_report(result, folder=None):
    """Write the timestamped report; returns its path, or None on failure."""
    try:
        target_folder = folder
        if not target_folder:
            try:
                target_folder = arcpy.env.scratchFolder
            except Exception:
                target_folder = None
        target_folder = target_folder or tempfile.gettempdir()
        os.makedirs(target_folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(target_folder, "lateral_report_%s.txt" % stamp)
        suffix = 1
        while os.path.exists(path):
            path = os.path.join(target_folder,
                                "lateral_report_%s_%d.txt" % (stamp, suffix))
            suffix += 1
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_report_text(result))
        return path
    except Exception as err:
        arcpy.AddWarning("Could not write the lateral report file: %r" % err)
        return None


def _report_to_gp(result):
    """Mirror the report summary into geoprocessing messages."""
    prefix = "Would insert" if result["preview"] else "Inserted"
    arcpy.AddMessage(
        "%s %d lateral(s) and %d main vertex(es) for %d service points."
        % (prefix,
           result["would_insert"] if result["preview"] else result["inserted"],
           result["would_insert_vertices"] if result["preview"]
           else result["vertices_inserted"],
           result["total_points"]))
    arcpy.AddMessage(
        "  build=%d  already connected=%d  too far=%d  on main=%d  "
        "duplicate point=%d  bad geometry=%d"
        % (result["counts"]["build"], result["counts"]["already_connected"],
           result["counts"]["too_far"], result["counts"]["on_main"],
           result["counts"]["duplicate_point"], result["counts"]["bad_geom"]))

    if result["percentiles"]:
        summary = "  ".join(
            "%s=%.2f" % (key, result["percentiles"][key])
            for key in ("p50", "p80", "p95", "p97", "p99")
            if key in result["percentiles"])
        arcpy.AddMessage(
            "Distance percentiles (%d unserved points, %s): %s"
            % (result["percentile_count"], result["distance_units"], summary))

    if result["already_connected"]:
        arcpy.AddMessage(
            "Skipped %d point(s) that already have a lateral within the "
            "connection tolerance." % result["counts"]["already_connected"])
    if result["duplicate_point"]:
        arcpy.AddWarning(
            "Skipped %d point(s) that share a coordinate with an earlier "
            "service point - one lateral each (OID%s): %s"
            % (len(result["duplicate_point"]),
               "" if len(result["duplicate_point"]) == 1 else "s",
               _oid_text(result["duplicate_point"])))
    if result["too_far"]:
        arcpy.AddWarning(
            "Skipped %d point(s) with no main within the max search distance "
            "(OID%s): %s" % (len(result["too_far"]),
                             "" if len(result["too_far"]) == 1 else "s",
                             _oid_text(result["too_far"])))
    if result["on_main"]:
        arcpy.AddMessage(
            "Skipped %d point(s) already on a main - a vertex is inserted "
            "instead of a zero-length lateral." % len(result["on_main"]))
    if result["bad_geom"]:
        arcpy.AddWarning(
            "Skipped %d point(s) with null geometry (OID%s): %s"
            % (len(result["bad_geom"]),
               "" if len(result["bad_geom"]) == 1 else "s",
               _oid_text(result["bad_geom"])))
    if result["unresolved"]:
        arcpy.AddWarning(
            "Could not place a main vertex for %d connection(s); their laterals "
            "end at the nearest point on the main instead (OID%s): %s"
            % (len(result["unresolved"]),
               "" if len(result["unresolved"]) == 1 else "s",
               _oid_text(result["unresolved"])))
    if result["degenerate"]:
        arcpy.AddWarning(
            "Skipped %d zero-length lateral(s) (OID%s): %s"
            % (len(result["degenerate"]),
               "" if len(result["degenerate"]) == 1 else "s",
               _oid_text(result["degenerate"])))
    if result["report_path"]:
        arcpy.AddMessage("Report written to: %s" % result["report_path"])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_laterals(points_fc, mains_fc, laterals_fc,
                      max_search_dist="100 Feet",
                      connection_tolerance="1 Foot",
                      skip_connected=True,
                      insert_main_vertices=True,
                      preview=False,
                      report_folder=None,
                      transactional=True):
    """Build a lateral from each service point to its nearest main.

    Parameters
    ----------
    points_fc : str
        Service point feature class or layer (Point).
    mains_fc : str
        Main line feature class or layer (Polyline). Vertices may be inserted.
    laterals_fc : str
        Existing line feature class the laterals are appended to (Polyline).
        Only SHAPE is written; other fields are left null.
    max_search_dist : str
        Linear unit ("100 Feet") or a bare number in the laterals' SR units.
    connection_tolerance : str
        Points nearer than this to a main or an existing lateral count as
        already connected; also the snap tolerance for main vertices.
    skip_connected : bool
        Skip points that already have a lateral within the tolerance. Leave on:
        turning it off duplicates laterals on every run.
    insert_main_vertices : bool
        Insert a vertex into the main at each connection so the trace network
        creates a junction.
    preview : bool
        Run the whole analysis and write the report, but make no geodatabase
        writes.
    report_folder : str or None
        Where to write the report; defaults to the scratch folder.
    transactional : bool
        Wrap the write phase in an arcpy.da.Editor transaction. The Pro toolbox
        leaves this off because Pro owns the edit session.

    Returns
    -------
    dict
        Counts, per-category OID lists, distance percentiles, resolved vertex
        counts and the report path.
    """
    _validate(points_fc, mains_fc, laterals_fc, preview)

    laterals_desc = arcpy.Describe(laterals_fc)
    target_sr = laterals_desc.spatialReference
    mains_sr = arcpy.Describe(mains_fc).spatialReference

    tolerance_sr = _linear_to_sr_units(connection_tolerance, target_sr)
    maximum_sr = _linear_to_sr_units(max_search_dist, target_sr)
    distance_units = getattr(target_sr, "linearUnitName", None) or "linear units"

    previous_overwrite = arcpy.env.overwriteOutput
    arcpy.env.overwriteOutput = True
    _delete_temps()

    result = {
        "preview": preview,
        "inputs": {"points": points_fc, "mains": mains_fc,
                   "laterals": laterals_fc},
        "max_search_dist": str(max_search_dist),
        "connection_tolerance": str(connection_tolerance),
        "skip_connected": bool(skip_connected),
        "insert_main_vertices": bool(insert_main_vertices),
        "laterals_sr": laterals_desc.spatialReference.name,
        "mains_sr": mains_sr.name,
        "distance_units": distance_units,
        "total_points": 0,
        "counts": {},
        "inserted": 0,
        "would_insert": 0,
        "vertices_inserted": 0,
        "would_insert_vertices": 0,
        "vertices_snapped": 0,
        "unresolved": [],
        "degenerate": [],
        "too_far": [],
        "already_connected": [],
        "on_main": [],
        "duplicate_point": [],
        "bad_geom": [],
        "too_far_distances": {},
        "percentiles": {},
        "percentile_count": 0,
        "report_path": None,
    }

    try:
        analysis = _analyze(points_fc, mains_fc, laterals_fc, target_sr,
                            maximum_sr, connection_tolerance,
                            skip_connected, tolerance_sr)
        categories = analysis["categories"]
        connections = analysis["connections"]
        build_connections = [c for c in connections if c["kind"] == "build"]

        result["total_points"] = analysis["total_points"]
        result["counts"] = {key: len(value)
                            for key, value in categories.items()}
        result["too_far"] = categories["too_far"]
        result["already_connected"] = categories["already_connected"]
        result["on_main"] = categories["on_main"]
        result["duplicate_point"] = categories["duplicate_point"]
        result["bad_geom"] = categories["bad_geom"]
        result["too_far_distances"] = analysis["too_far_distances"]
        result["percentiles"] = analysis["percentiles"]
        result["percentile_count"] = analysis["percentile_count"]

        if preview:
            vertex_plan = (
                _apply_vertices(mains_fc, mains_sr, target_sr, connections,
                                tolerance_sr, write=False)
                if insert_main_vertices and connections else
                {"resolved": {}, "inserted": 0, "snapped": 0, "unresolved": []})
            result["would_insert_vertices"] = vertex_plan["inserted"]
            result["vertices_snapped"] = vertex_plan["snapped"]
            result["unresolved"] = vertex_plan["unresolved"]
            result["would_insert"] = sum(
                1 for connection in build_connections
                if _length_sq(connection, vertex_plan["resolved"]) > 1e-18)
            arcpy.AddMessage(
                "Preview: no laterals or vertices were written.")
        else:
            def write_phase():
                plan = (
                    _apply_vertices(mains_fc, mains_sr, target_sr, connections,
                                    tolerance_sr, write=True)
                    if insert_main_vertices and connections else
                    {"resolved": {}, "inserted": 0, "snapped": 0,
                     "unresolved": []})
                inserted, degenerate = _insert_laterals(
                    laterals_fc, build_connections, plan["resolved"], target_sr)
                return plan, inserted, degenerate

            laterals_ws = _workspace_of(laterals_fc)
            mains_ws = _workspace_of(mains_fc)
            mixed_workspaces = (
                bool(laterals_ws) and bool(mains_ws)
                and os.path.normcase(laterals_ws) != os.path.normcase(mains_ws))
            if transactional and mixed_workspaces and insert_main_vertices:
                raise arcpy.ExecuteError(
                    "Mains and laterals must be in the same workspace for a "
                    "transactional run, because the vertex write must be rolled "
                    "back with the lateral write. Run again with "
                    "--no-transaction (and the edit session closed), or move "
                    "one of the datasets into the other's workspace: %s vs "
                    "%s." % (laterals_ws, mains_ws))
            elif mixed_workspaces:
                arcpy.AddWarning(
                    "Mains and laterals are in different workspaces; the write "
                    "is not fully transactional (%s vs %s)."
                    % (laterals_ws, mains_ws))

            if transactional:
                plan, inserted, degenerate = _run_in_edit_session(
                    laterals_ws, write_phase)
            else:
                plan, inserted, degenerate = write_phase()

            result["inserted"] = inserted
            result["would_insert"] = len(build_connections)
            result["vertices_inserted"] = plan["inserted"]
            result["would_insert_vertices"] = plan["inserted"]
            result["vertices_snapped"] = plan["snapped"]
            result["unresolved"] = plan["unresolved"]
            result["degenerate"] = degenerate
    finally:
        _cleanup()
        arcpy.env.overwriteOutput = previous_overwrite
        arcpy.ResetProgressor()

    result["report_path"] = _write_report(result, report_folder)
    _report_to_gp(result)
    return result


def _length_sq(connection, resolved):
    """Squared lateral length after vertex resolution (0.0 means degenerate)."""
    x2, y2 = resolved.get(connection["src_oid"],
                          (connection["nx"], connection["ny"]))
    return (connection["px"] - x2) ** 2 + (connection["py"] - y2) ** 2


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest_create(name, geometry_type, spatial_reference, has_z=False):
    path = os.path.join(_MEM_WS, name)
    if arcpy.Exists(path):
        arcpy.management.Delete(path)
    arcpy.management.CreateFeatureclass(
        _MEM_WS, name, geometry_type,
        template=None,
        has_m="DISABLED",
        has_z="ENABLED" if has_z else "DISABLED",
        spatial_reference=spatial_reference)
    return path


def _selftest_features(path):
    """Per feature, per part, the [(x, y), ...] vertex list."""
    features = []
    with arcpy.da.SearchCursor(path, ["SHAPE@"]) as reader:
        for (geometry,) in reader:
            features.append(
                [[(pt.X, pt.Y) for pt in geometry.getPart(i) if pt is not None]
                 for i in range(geometry.partCount)])
    return features


def _selftest_count(path):
    return int(arcpy.management.GetCount(path)[0])


def _selftest_basic(spatial_reference, report_folder):
    """Classification, lateral geometry, vertex order, preview and idempotency."""
    mains = _selftest_create("st_mains", "Polyline", spatial_reference)
    points = _selftest_create("st_points", "Point", spatial_reference)
    laterals = _selftest_create("st_laterals", "Polyline", spatial_reference)

    with arcpy.da.InsertCursor(mains, ["SHAPE@"]) as cursor:
        cursor.insertRow([arcpy.Polyline(arcpy.Array(
            [arcpy.Point(0, 0), arcpy.Point(1000, 0)]), spatial_reference)])
        cursor.insertRow([arcpy.Polyline(arcpy.Array(
            [arcpy.Point(1000, 0), arcpy.Point(1000, 1000)]), spatial_reference)])

    with arcpy.da.InsertCursor(laterals, ["SHAPE@"]) as cursor:
        cursor.insertRow([arcpy.Polyline(arcpy.Array(
            [arcpy.Point(300, 100), arcpy.Point(300, 0)]), spatial_reference)])

    with arcpy.da.InsertCursor(points, ["SHAPE@"]) as cursor:
        cursor.insertRow([arcpy.Point(500, 100)])     # 1 build
        cursor.insertRow([arcpy.Point(1500, 100)])    # 2 build
        cursor.insertRow([arcpy.Point(500, 5000)])    # 3 too far
        cursor.insertRow([arcpy.Point(200, 0)])       # 4 on a main
        cursor.insertRow([arcpy.Point(300, 100)])     # 5 already has a lateral
        cursor.insertRow([arcpy.Point(500, 100)])     # 6 same spot as point 1

    mains_before = _selftest_count(mains)

    # --- preview: analysis and report, but no writes -----------------------
    preview = generate_laterals(points, mains, laterals, "2000 Meters",
                                "1 Meter", preview=True,
                                report_folder=report_folder,
                                transactional=False)
    assert _selftest_count(laterals) == 1, "preview inserted a lateral"
    assert _selftest_count(mains) == mains_before, "preview changed mains"
    assert _selftest_features(mains)[0][0] == [(0, 0), (1000, 0)], \
        "preview changed main geometry: %s" % (_selftest_features(mains)[0][0],)
    assert preview["would_insert"] == 2, \
        "preview would_insert should be 2, got %s" % preview["would_insert"]
    assert preview["would_insert_vertices"] == 3, \
        "preview would_insert_vertices should be 3, got %s" % \
        preview["would_insert_vertices"]
    assert preview["report_path"] and os.path.exists(preview["report_path"]), \
        "preview should still write the report"

    # --- real run ----------------------------------------------------------
    first = generate_laterals(points, mains, laterals, "2000 Meters", "1 Meter",
                              report_folder=report_folder, transactional=False)

    assert first["counts"] == {"build": 2, "too_far": 1,
                               "already_connected": 1, "on_main": 1,
                               "duplicate_point": 1, "bad_geom": 0}, \
        "unexpected category counts: %s" % (first["counts"],)
    assert sum(first["counts"].values()) == first["total_points"], \
        "categories do not account for every point"
    assert first["inserted"] == first["counts"]["build"] == 2, \
        "inserted (%s) should equal the build count (%s)" % (
            first["inserted"], first["counts"]["build"])
    assert first["duplicate_point"] == [6], \
        "duplicate_point should be [6], got %s" % (first["duplicate_point"],)
    assert first["too_far"] == [3], "too_far should be [3], got %s" % (
        first["too_far"],)
    assert first["on_main"] == [4], "on_main should be [4], got %s" % (
        first["on_main"],)
    assert first["already_connected"] == [5], \
        "already_connected should be [5], got %s" % (first["already_connected"],)
    assert first["vertices_inserted"] == 3, \
        "expected 3 vertices, got %s" % first["vertices_inserted"]

    # Feature count unchanged; vertices inserted in ascending order along main 1.
    assert _selftest_count(mains) == mains_before, \
        "vertex insertion changed the mains feature count"
    features = _selftest_features(mains)
    assert features[0][0] == [(0, 0), (200, 0), (500, 0), (1000, 0)], \
        "main 1 vertices out of order: %s" % (features[0][0],)
    assert features[1][0] == [(1000, 0), (1000, 100), (1000, 1000)], \
        "main 2 vertices wrong: %s" % (features[1][0],)

    # New lateral geometries, and every far endpoint sits on a main vertex.
    got = []
    with arcpy.da.SearchCursor(laterals, ["SHAPE@"]) as reader:
        for (line,) in reader:
            got.append([(pt.X, pt.Y) for pt in line.getPart(0) if pt is not None])
    assert len(got) == 3, "expected 3 laterals (1 pre-existing), got %d" % len(got)
    for line in got[1:]:
        assert len(line) == 2, "laterals must be 2-point lines, got %s" % (line,)
    expected = {(500, 100): (500, 0), (1500, 100): (1000, 100)}
    vertices = {vertex for parts in features for part in parts for vertex in part}
    for line in got[1:]:
        start = line[0]
        assert start in expected, "unexpected lateral start: %s" % (start,)
        assert line[1] == expected[start], \
            "lateral from %s should end at %s, got %s" % (
                start, expected[start], line[1])
        assert line[1] in vertices, \
            "far endpoint %s is not a main vertex (%s)" % (line[1], sorted(vertices))

    # --- idempotent second run --------------------------------------------
    second = generate_laterals(points, mains, laterals, "2000 Meters", "1 Meter",
                               report_folder=report_folder, transactional=False)
    assert second["inserted"] == 0, \
        "second run inserted %d laterals" % second["inserted"]
    assert second["vertices_inserted"] == 0, \
        "second run inserted %d vertices" % second["vertices_inserted"]
    assert _selftest_count(laterals) == 3, "second run duplicated laterals"
    assert second["counts"]["already_connected"] == 4, \
        "second run should skip 4 connected points, got %s" % (
            second["counts"]["already_connected"],)


def _selftest_multipart_z(spatial_reference, report_folder):
    """A multipart and a Z-enabled main rebuild without error."""
    mains = _selftest_create("st_mains_z", "Polyline", spatial_reference,
                             has_z=True)
    points = _selftest_create("st_points_z", "Point", spatial_reference)
    laterals = _selftest_create("st_laterals_z", "Polyline", spatial_reference,
                                has_z=True)

    with arcpy.da.InsertCursor(mains, ["SHAPE@"]) as cursor:
        cursor.insertRow([arcpy.Polyline(arcpy.Array([
            arcpy.Array([arcpy.Point(0, 0, 10), arcpy.Point(100, 0, 20)]),
            arcpy.Array([arcpy.Point(0, 50, 10), arcpy.Point(100, 50, 20)]),
        ]), spatial_reference, True, False)])

    with arcpy.da.InsertCursor(points, ["SHAPE@"]) as cursor:
        cursor.insertRow([arcpy.Point(50, 10)])

    result = generate_laterals(points, mains, laterals, "500 Meters", "1 Meter",
                               report_folder=report_folder, transactional=False)
    assert result["inserted"] == 1, "expected 1 Z-aware lateral, got %s" % (
        result["inserted"],)
    assert result["vertices_inserted"] == 1, \
        "expected 1 vertex, got %s" % result["vertices_inserted"]

    features = _selftest_features(mains)
    assert len(features) == 1, "expected a single multipart main feature"
    parts = features[0]
    assert len(parts) == 2, \
        "multipart structure lost (parts=%d)" % len(parts)
    assert len(parts[0]) == 3, \
        "part 1 should have 3 vertices, got %s" % (parts[0],)
    assert len(parts[1]) == 2, "part 2 should be untouched"
    middle = parts[0][1]
    assert abs(middle[0] - 50) < 1e-9 and abs(middle[1]) < 1e-9, \
        "vertex inserted at the wrong place: %s" % (middle,)

    with arcpy.da.SearchCursor(mains, ["SHAPE@"]) as reader:
        (geometry,) = next(iter(reader))
        inserted_vertex = [pt for pt in geometry.getPart(0) if pt is not None][1]
        assert abs((inserted_vertex.Z or 0) - 15.0) < 1e-9, \
            "Z should be interpolated to 15.0, got %s" % (inserted_vertex.Z,)

    lateral_parts = _selftest_features(laterals)
    assert lateral_parts and lateral_parts[0][0] == [(50, 10), (50, 0)], \
        "Z lateral endpoint wrong: %s" % (lateral_parts,)
    assert arcpy.Describe(laterals).hasZ, \
        "laterals target should still be Z-enabled"


def selftest(report_folder=None):
    """Synthetic end-to-end checks in the memory workspace; raises on failure."""
    previous_workspace = arcpy.env.workspace
    previous_overwrite = arcpy.env.overwriteOutput
    arcpy.env.workspace = _MEM_WS
    arcpy.env.overwriteOutput = True
    spatial_reference = arcpy.SpatialReference(26911)   # NAD83 / UTM 11N, meters
    created = ("st_mains", "st_points", "st_laterals",
               "st_mains_z", "st_points_z", "st_laterals_z")
    try:
        for name in created:
            if arcpy.Exists(os.path.join(_MEM_WS, name)):
                arcpy.management.Delete(os.path.join(_MEM_WS, name))
        _selftest_basic(spatial_reference, report_folder)
        _selftest_multipart_z(spatial_reference, report_folder)
        print("SELFTEST PASSED")
        return True
    finally:
        _cleanup()
        for name in created:
            try:
                if arcpy.Exists(os.path.join(_MEM_WS, name)):
                    arcpy.management.Delete(os.path.join(_MEM_WS, name))
            except Exception:
                pass
        arcpy.env.workspace = previous_workspace
        arcpy.env.overwriteOutput = previous_overwrite
