import csv
import os


def write_report(rows: list[dict], path) -> None:
    """
    Write rows as a CSV file to path using the csv module.

    Args:
        rows: A list of dicts to write as CSV rows.
        path: The file path where the CSV will be written.

    The CSV header is the UNION of all keys across all dicts in rows.
    Missing keys in a row are filled with empty strings.
    If rows is empty, writes an empty file (no crash).
    Creates parent directory if it doesn't exist.
    """
    # Create parent directory if needed
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    # Handle empty rows list
    if not rows:
        with open(path, 'w', newline='') as f:
            pass
        return

    # Compute the union of all keys across all rows
    # Preserve insertion order by using dict.keys() which maintains order in Python 3.7+
    fieldnames_set = set()
    fieldnames_list = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames_set:
                fieldnames_set.add(key)
                fieldnames_list.append(key)

    # Write CSV with union of fieldnames
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames_list, restval='')
        writer.writeheader()
        writer.writerows(rows)
