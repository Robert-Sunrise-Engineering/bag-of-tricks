# -*- coding: utf-8 -*-
"""
lat_from_service.py - Create lateral lines from water service points.

Thin command-line / ArcGIS Pro script-tool wrapper around lateral_core.py. All
of the work - analysis, main vertex insertion, lateral construction and the
report - lives in lateral_core.generate_laterals(); this file only reads
parameters and reports failures.

For each service point, a two-point lateral is appended to an existing line
feature class, running from the point to its nearest main. A vertex is inserted
into the main at the connection so the trace network creates a junction. Points
that already have a lateral (within the connection tolerance), sit on a main,
have no main within the maximum search distance, or share a coordinate with
another service point are skipped and reported.

Parameters (script tool, in order):
    1. Service points        - point feature class/layer
    2. Mains                 - line feature class/layer (vertices may be inserted)
    3. Laterals target       - EXISTING line feature class to append into
                               (only SHAPE is written; other fields stay null)
    4. Max search distance   - linear unit, e.g. "100 Feet"
    5. Connection tolerance  - linear unit, e.g. "1 Foot"
    6. Skip already connected - boolean ("true"/"false")
    7. Insert vertices in mains - boolean
    8. Preview only          - boolean
    9. Report folder         - folder

Run:
    propy lat_from_service.py <points> <mains> <laterals> "100 Feet"
    propy lat_from_service.py <points> <mains> <laterals> "100 Feet" --preview
    propy lat_from_service.py <points> <mains> <laterals> -t "1 Foot" --no-vertices
Self-test (synthetic data, no inputs):
    propy lat_from_service.py --selftest
"""

import sys

import arcpy

import lateral_core

_DEFAULT_MAX_DIST = "100 Feet"
_DEFAULT_TOLERANCE = "1 Foot"


def _as_bool(text, default):
    """Read a boolean from a script-tool text value or a CLI flag."""
    if text is None:
        return default
    value = str(text).strip().lower()
    if not value:
        return default
    if value in ("true", "1", "yes", "y"):
        return True
    if value in ("false", "0", "no", "n"):
        return False
    return default


def _gp_text(index, default=""):
    """Read an optional script-tool parameter without failing when unset."""
    try:
        value = arcpy.GetParameterAsText(index)
    except Exception:
        return default
    if value is None or value == "":
        return default
    return value


def _run_from_gp():
    """Parameter values supplied by the ArcGIS Pro script tool."""
    required = [_gp_text(index) for index in range(4)]
    if not all(required):
        raise arcpy.ExecuteError(
            "Service points, mains, laterals target and maximum search distance "
            "are required.")
    points, mains, laterals, max_search_dist = required

    lateral_core.generate_laterals(
        points, mains, laterals,
        max_search_dist=max_search_dist,
        connection_tolerance=_gp_text(4, _DEFAULT_TOLERANCE),
        skip_connected=_as_bool(_gp_text(5, "true"), True),
        insert_main_vertices=_as_bool(_gp_text(6, "true"), True),
        preview=_as_bool(_gp_text(7, "false"), False),
        report_folder=_gp_text(8, None) or None,
        transactional=True)
    return 0


def _run_from_cli(argv):
    """Command-line parameters (also used by propy)."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Append a lateral from each service point to its nearest "
                    "main, inserting a vertex into the main at each connection.")
    parser.add_argument("points", help="service point feature class")
    parser.add_argument("mains", help="main line feature class")
    parser.add_argument("laterals", help="existing line feature class to append into")
    parser.add_argument("max_search_dist", nargs="?",
                        default=_DEFAULT_MAX_DIST,
                        help='maximum search distance, e.g. "100 Feet" (default)')
    parser.add_argument("-t", "--tolerance", dest="connection_tolerance",
                        default=_DEFAULT_TOLERANCE,
                        help='connection tolerance, e.g. "1 Foot" (default)')
    parser.add_argument("--no-skip-connected", dest="skip_connected",
                        action="store_false",
                        help="do not skip points that already have a lateral "
                             "(duplicates laterals - leave off)")
    parser.add_argument("--no-vertices", dest="insert_main_vertices",
                        action="store_false",
                        help="do not insert vertices into the mains")
    parser.add_argument("--preview", action="store_true",
                        help="analyze and report only; make no geodatabase writes")
    parser.add_argument("--report-folder", default=None,
                        help="folder for the timestamped report "
                             "(default: the scratch folder)")
    parser.add_argument("--no-transaction", dest="transactional",
                        action="store_false",
                        help="do not wrap the writes in an edit-session transaction")
    parser.add_argument("--selftest", action="store_true",
                        help="run synthetic checks and exit")

    args = parser.parse_args(argv)
    if args.selftest:
        lateral_core.selftest(args.report_folder)
        return 0

    lateral_core.generate_laterals(
        args.points, args.mains, args.laterals,
        max_search_dist=args.max_search_dist,
        connection_tolerance=args.connection_tolerance,
        skip_connected=args.skip_connected,
        insert_main_vertices=args.insert_main_vertices,
        preview=args.preview,
        report_folder=args.report_folder,
        transactional=args.transactional)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        report_folder = None
        if "--report-folder" in argv:
            index = argv.index("--report-folder")
            if index + 1 < len(argv):
                report_folder = argv[index + 1]
        return lateral_core.selftest(report_folder) or 0
    if argv:
        return _run_from_cli(argv)
    return _run_from_gp()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except arcpy.ExecuteError as err:
        arcpy.AddError(str(err))
        print(str(err), file=sys.stderr)
        sys.exit(1)
    except Exception as err:      # noqa: BLE001 - report anything unexpected
        arcpy.AddError("Unexpected failure: %r" % err)
        print("Unexpected failure: %r" % err, file=sys.stderr)
        sys.exit(1)
