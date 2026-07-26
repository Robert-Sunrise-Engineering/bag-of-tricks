"""Apply updates and appends to AGOL feature layers."""


def apply_updates(layer, updates: list[dict]) -> list[dict]:
    """
    Apply attribute and geometry updates to existing features.

    Args:
        layer: An AGOL FeatureLayer object with an edit_features method.
        updates: List of dicts, each shaped {"attributes": {...}, "geometry": {...}}.
                 The attributes dict must include OBJECTID or GlobalID to identify
                 which record to update.

    Returns:
        List of normalized result dicts (one per input), each shaped:
        {
            "input_ref": <the corresponding item from updates>,
            "success": <bool>,
            "result_oid": <objectId or globalId from result, or None>,
            "error": <str(error) if present, else None>
        }
    """
    result = layer.edit_features(updates=updates)
    normalized = []

    for i, update_result in enumerate(result["updateResults"]):
        success = update_result.get("success", False)
        result_oid = update_result.get("objectId") or update_result.get("globalId")
        error = None
        if not success and "error" in update_result:
            error = str(update_result["error"])

        normalized.append({
            "input_ref": updates[i],
            "success": success,
            "result_oid": result_oid,
            "error": error,
        })

    return normalized


def apply_appends(layer, adds: list[dict]) -> list[dict]:
    """
    Apply new feature additions to a feature layer.

    Args:
        layer: An AGOL FeatureLayer object with an edit_features method.
        adds: List of dicts, each shaped {"attributes": {...}, "geometry": {...}}.
              These are new features (no existing OBJECTID).

    Returns:
        List of normalized result dicts (one per input), each shaped:
        {
            "input_ref": <the corresponding item from adds>,
            "success": <bool>,
            "result_oid": <objectId or globalId from result, or None>,
            "error": <str(error) if present, else None>
        }
    """
    result = layer.edit_features(adds=adds)
    normalized = []

    for i, add_result in enumerate(result["addResults"]):
        success = add_result.get("success", False)
        result_oid = add_result.get("objectId") or add_result.get("globalId")
        error = None
        if not success and "error" in add_result:
            error = str(add_result["error"])

        normalized.append({
            "input_ref": adds[i],
            "success": success,
            "result_oid": result_oid,
            "error": error,
        })

    return normalized
