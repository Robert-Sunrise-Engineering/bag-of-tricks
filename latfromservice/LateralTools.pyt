# -*- coding: utf-8 -*-
"""
LateralTools.pyt - Generate Service Laterals (ArcGIS Pro Python toolbox).

Thin front end over lateral_core.py, which must sit in the same folder as this
file. The tool finds each service point's nearest main, inserts a vertex into
the main at the connection (so the trace network creates a junction), and
appends a two-point lateral from the service point to that vertex. Points that
already have a lateral, sit on a main, have no main within the search distance,
or share a coordinate with another service point are skipped and listed in a
timestamped report.

Everything project-specific is a parameter; nothing is hard-coded, so the
toolbox can be copied to another project unchanged. The tool does not start its
own edit session - ArcGIS Pro owns editing. If an edit session is open on the
mains or laterals, the tool fails early and asks for it to be closed.

Parameters
----------
1. Service Points                    Point feature layer, required
2. Mains                             Polyline feature layer, required
3. Target Laterals (appended to)     Polyline feature layer, required
4. Maximum Search Distance           Linear unit, default 100 Feet
5. Connection Tolerance              Linear unit, default 1 Foot
6. Skip Points Already Connected     Boolean, default True
7. Insert Vertices in Mains          Boolean, default True
8. Preview Only (No Writes)          Boolean, default False
9. Report Folder                     Folder, optional (default: scratch folder)
"""

import os
import sys

import arcpy

ENGINE_MODULE = "lateral_core"


def _load_engine():
    """Import lateral_core.py from beside this toolbox file."""
    try:
        import lateral_core
        return lateral_core
    except ImportError:
        pass

    candidates = []
    try:
        candidates.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    try:
        candidates.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    except Exception:
        pass
    candidates.append(os.getcwd())

    for folder in candidates:
        if folder and os.path.isfile(os.path.join(folder, ENGINE_MODULE + ".py")):
            if folder not in sys.path:
                sys.path.insert(0, folder)
            break
    try:
        import lateral_core
        return lateral_core
    except ImportError:
        raise arcpy.ExecuteError(
            "%s.py must be kept in the same folder as LateralTools.pyt "
            "(or on the Python path). Looked in: %s"
            % (ENGINE_MODULE, ", ".join(c for c in candidates if c)))


class Toolbox(object):
    def __init__(self):
        self.label = "Service Laterals"
        self.alias = "ServiceLaterals"
        self.tools = [GenerateServiceLaterals]


class GenerateServiceLaterals(object):
    def __init__(self):
        self.label = "Generate Service Laterals"
        self.description = (
            "Append a two-point lateral from each water service point to its "
            "nearest main and insert a vertex into the main at the connection. "
            "Points already connected (within the connection tolerance), on a "
            "main, or beyond the maximum search distance are skipped and listed "
            "in a timestamped report.")
        self.canRunInBackground = False
        self.category = "Service Laterals"

    def getParameterInfo(self):
        points = arcpy.Parameter(
            displayName="Service Points",
            name="points",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input")
        points.filter.list = ["Point"]

        mains = arcpy.Parameter(
            displayName="Mains",
            name="mains",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input")
        mains.filter.list = ["Polyline"]

        laterals = arcpy.Parameter(
            displayName="Target Laterals (appended to)",
            name="laterals",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input")
        laterals.filter.list = ["Polyline"]

        maximum_distance = arcpy.Parameter(
            displayName="Maximum Search Distance",
            name="max_search_dist",
            datatype="GPLinearUnit",
            parameterType="Required",
            direction="Input")
        maximum_distance.value = "100 Feet"

        tolerance = arcpy.Parameter(
            displayName="Connection Tolerance",
            name="connection_tolerance",
            datatype="GPLinearUnit",
            parameterType="Required",
            direction="Input")
        tolerance.value = "1 Foot"

        skip_connected = arcpy.Parameter(
            displayName="Skip Points Already Connected",
            name="skip_connected",
            datatype="GPBoolean",
            parameterType="Required",
            direction="Input")
        skip_connected.value = True

        insert_vertices = arcpy.Parameter(
            displayName="Insert Vertices in Mains",
            name="insert_main_vertices",
            datatype="GPBoolean",
            parameterType="Required",
            direction="Input")
        insert_vertices.value = True

        preview = arcpy.Parameter(
            displayName="Preview Only (No Writes)",
            name="preview",
            datatype="GPBoolean",
            parameterType="Required",
            direction="Input")
        preview.value = False

        report_folder = arcpy.Parameter(
            displayName="Report Folder",
            name="report_folder",
            datatype="DEFolder",
            parameterType="Optional",
            direction="Input")

        return [points, mains, laterals, maximum_distance, tolerance,
                skip_connected, insert_vertices, preview, report_folder]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        # Deliberately empty: no analysis here. updateMessages fires on every
        # dialog change and a Near run over ~6k points would freeze the dialog.
        return

    def updateMessages(self, parameters):
        """Cheap input checks only - this runs on every dialog change."""
        points, mains, laterals = parameters[0], parameters[1], parameters[2]
        expected = ((points, "Point", "Service Points"),
                    (mains, "Polyline", "Mains"),
                    (laterals, "Polyline", "Target Laterals"))
        for parameter, shape_type, label in expected:
            parameter.clearMessage()
            if not parameter.value:
                continue
            try:
                actual = arcpy.Describe(parameter.value).shapeType
            except Exception:
                continue
            if actual != shape_type:
                parameter.setErrorMessage(
                    "%s must be a %s feature class (got %s)."
                    % (label, shape_type, actual))

        if mains.value and laterals.value:
            try:
                if (arcpy.Describe(mains.value).catalogPath.lower()
                        == arcpy.Describe(laterals.value).catalogPath.lower()):
                    laterals.setErrorMessage(
                        "The mains and the laterals target must be separate "
                        "feature classes.")
            except Exception:
                pass
        return

    def execute(self, parameters, messages):
        engine = _load_engine()

        points = parameters[0].valueAsText
        mains = parameters[1].valueAsText
        laterals = parameters[2].valueAsText
        maximum_distance = parameters[3].valueAsText or "100 Feet"
        tolerance = parameters[4].valueAsText or "1 Foot"
        skip_connected = bool(parameters[5].value)
        insert_vertices = bool(parameters[6].value)
        preview = bool(parameters[7].value)
        report_folder = parameters[8].valueAsText or None

        # transactional=False: Pro owns the edit session for this tool.
        engine.generate_laterals(
            points, mains, laterals,
            max_search_dist=maximum_distance,
            connection_tolerance=tolerance,
            skip_connected=skip_connected,
            insert_main_vertices=insert_vertices,
            preview=preview,
            report_folder=report_folder,
            transactional=False)
