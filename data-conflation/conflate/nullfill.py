def is_null(value) -> bool:
    """
    Return True if value is None or a whitespace-only string.
    Return False for everything else, including 0, 0.0, and False.

    Args:
        value: Any value to check for nullness

    Returns:
        bool: True if value is None or a blank/whitespace-only string, False otherwise
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def build_field_updates(captured_attrs: dict, authoritative_attrs: dict, field_map: dict, excluded_fields: set) -> dict:
    """
    Build a dict of field updates from captured attributes to fill null authoritative values.

    Constructs the effective set of (captured_field, authoritative_field) pairs to consider:
    - All pairs from field_map (captured_field_name -> authoritative_field_name)
    - Plus any field names present in both dicts that aren't already keys in field_map

    For each pair, includes authoritative_field: captured_value in the result only if:
    - The authoritative field's current value is null
    - The captured field's value is not null
    - The authoritative field is not in excluded_fields

    Args:
        captured_attrs: Dict of captured feature attributes
        authoritative_attrs: Dict of authoritative feature attributes
        field_map: Dict mapping captured_field_name -> authoritative_field_name (for renamed fields)
        excluded_fields: Set of authoritative field names to never update

    Returns:
        Dict of authoritative_field_name -> captured_value for fields to update
    """
    result = {}

    # Build effective set of (captured_field, authoritative_field) pairs
    pairs = {}

    # First, add all mapped pairs from field_map
    pairs.update(field_map)

    # Then, add any keys present in both dicts that aren't already mapped
    for field_name in captured_attrs:
        if field_name in authoritative_attrs and field_name not in field_map:
            pairs[field_name] = field_name

    # Process each pair and build updates
    for captured_field, authoritative_field in pairs.items():
        # Include in updates only if:
        # 1. Authoritative field is null
        # 2. Captured field is not null
        # 3. Authoritative field is not excluded
        if (is_null(authoritative_attrs.get(authoritative_field)) and
            not is_null(captured_attrs.get(captured_field)) and
            authoritative_field not in excluded_fields):
            result[authoritative_field] = captured_attrs[captured_field]

    return result
