def format_threshold_both_units(value: float, source_units: str) -> str:
    """
    Format a threshold value showing both its original and converted units.

    Args:
        value: The threshold value.
        source_units: Either "meters" or "feet" (case-insensitive).

    Returns:
        A string showing both units, e.g., "12.0 ft (3.66 m)" or "3.66 m (12.01 ft)".

    Raises:
        ValueError: If source_units is neither "meters" nor "feet".
    """
    source_units_lower = source_units.lower()

    # Conversion factor: 1 meter = 3.28084 feet
    if source_units_lower == "meters":
        converted_value = value * 3.28084
        result = f"{value:.2f} m ({converted_value:.2f} ft)"
    elif source_units_lower == "feet":
        converted_value = value / 3.28084
        result = f"{value:.2f} ft ({converted_value:.2f} m)"
    else:
        raise ValueError(f"Invalid source_units: {source_units}. Must be 'meters' or 'feet'.")

    return result
