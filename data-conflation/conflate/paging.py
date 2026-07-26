"""Feature layer paging and fetching utilities for AGOL feature layers."""


def fetch_all_features(layer):
    """
    Fetch every single feature in an AGOL feature layer with full pagination.

    Retrieves all features from the layer by using return_all_records=True,
    which handles internal pagination transparently. Validates the fetch by
    comparing the fetched count against the layer's reported total count.

    Requests out_sr=4326 (WGS84) so returned geometries are always lon/lat
    degrees regardless of the layer's native spatial reference — callers
    (matching/geodesic distance) assume WGS84 lon/lat and never reproject.

    Args:
        layer: An arcgis.features.FeatureLayer object

    Returns:
        list: A list of feature dictionaries, each containing attributes and geometry

    Raises:
        RuntimeError: If the fetched feature count doesn't match the layer's reported count,
                     indicating that paging may have truncated results
    """
    # Fetch all features with pagination handled internally by arcgis API
    feature_set = layer.query(
        where="1=1", out_fields="*", return_all_records=True, out_sr=4326
    )

    # Extract features from FeatureSet and convert to dictionaries
    features = []
    for feature in feature_set.features:
        # Try to use as_dict() if available, otherwise use fallback
        if hasattr(feature, 'as_dict') and callable(feature.as_dict):
            features.append(feature.as_dict())
        else:
            features.append({
                "attributes": feature.attributes,
                "geometry": feature.geometry
            })

    # Verify the count matches what the layer reports
    expected_count = layer.query(where="1=1", return_count_only=True)
    fetched_count = len(features)

    if fetched_count != expected_count:
        raise RuntimeError(
            f"Fetched {fetched_count} features but layer reports {expected_count} — "
            f"paging may have truncated results"
        )

    return features
