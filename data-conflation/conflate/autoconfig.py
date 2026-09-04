"""Pure matching/merge logic for ``--auto-configure``.

No ``arcgis`` import here -- this module consumes only the plain dicts that
``conflate.gis_client.list_service_sublayers`` returns and produces plain
layer-config dicts that ``conflate.config.validate_layer_config`` accepts.
It follows the codebase's existing pure/AGOL-integration split
(``matching.py``/``nullfill.py``/``fields.py`` are pure; ``gis_client.py``/
``paging.py``/``apply.py`` talk to AGOL).
"""

from conflate.config import validate_layer_config

POINT = "esriGeometryPoint"


def match_sublayers(authoritative_sublayers, captured_sublayers) -> dict:
    """Match two services' sublayers by ``name``, case-insensitively.

    The AGOL sublayer ``name`` property's casing varies by org -- it can be
    PascalCase (``Water_Hydrants``) while a hand-written config key for the
    same layer is lowercase snake_case (``water_hydrants``). Matching is
    therefore done on ``name.lower()`` so a service pair aligns regardless of
    casing; the verbatim authoritative-side name is carried through as the
    pair's ``name`` (the authoritative service is the system of record, and
    it's the key used for genuinely-new entries -- see ``merge_layers_config``).

    Args:
        authoritative_sublayers: list of sublayer dicts from
            ``list_service_sublayers`` (each has ``id``/``name``/
            ``geometry_type``/``url``).
        captured_sublayers: same shape, for the captured service.

    Returns a dict with:
      * ``matched``: list of ``{"name", "authoritative_url",
        "captured_url", "authoritative_geometry_type",
        "captured_geometry_type"}``, one per canonical name that appears
        exactly once on each side. ``name`` is the authoritative side's
        verbatim name.
      * ``auth_only``: verbatim names present only on the authoritative side.
      * ``captured_only``: verbatim names present only on the captured side.
      * ``ambiguous``: canonical (lowercased) names repeated within one or
        both sides -- excluded from every other bucket, since a name match
        is meaningless when a name isn't unique. Each entry is
        ``{"authoritative_names", "captured_names"}``, the verbatim
        (as-cased) names found on each side under that canonical name, so a
        report can point at the actual sublayers to disambiguate rather than
        just the lowercased key.
    """
    auth_by_lower = {}
    for sub in authoritative_sublayers:
        auth_by_lower.setdefault(sub["name"].lower(), []).append(sub)
    cap_by_lower = {}
    for sub in captured_sublayers:
        cap_by_lower.setdefault(sub["name"].lower(), []).append(sub)

    all_lower = set(auth_by_lower) | set(cap_by_lower)
    matched, auth_only, captured_only, ambiguous = [], [], [], []
    for lower_name in sorted(all_lower):
        auth_entries = auth_by_lower.get(lower_name, [])
        cap_entries = cap_by_lower.get(lower_name, [])
        n_auth = len(auth_entries)
        n_cap = len(cap_entries)
        if n_auth > 1 or n_cap > 1:
            ambiguous.append(
                {
                    "authoritative_names": [e["name"] for e in auth_entries],
                    "captured_names": [e["name"] for e in cap_entries],
                }
            )
            continue
        if n_auth == 1 and n_cap == 1:
            a, c = auth_entries[0], cap_entries[0]
            matched.append(
                {
                    "name": a["name"],
                    "authoritative_url": a["url"],
                    "captured_url": c["url"],
                    "authoritative_geometry_type": a["geometry_type"],
                    "captured_geometry_type": c["geometry_type"],
                }
            )
        elif n_auth == 1 and n_cap == 0:
            auth_only.append(auth_entries[0]["name"])
        elif n_auth == 0 and n_cap == 1:
            captured_only.append(cap_entries[0]["name"])

    return {
        "matched": matched,
        "auth_only": auth_only,
        "captured_only": captured_only,
        "ambiguous": ambiguous,
    }


def classify_matches(matched: list[dict]) -> dict:
    """Bucket matched sublayer pairs by geometry type.

    Only point-on-point pairs are emittable into config (this tool only
    conflates point layers -- see ``gis_client.validate_geometry_type``).

    Returns a dict with:
      * ``point``: both sides ``esriGeometryPoint`` -- emittable.
      * ``non_point``: both sides agree but aren't point -- skip+warn.
      * ``geometry_mismatch``: sides disagree -- skip+warn.
      * ``unknown_geometry``: either side's ``geometry_type`` is ``None``
        (the root-service summary didn't include it) -- caller resolves with
        a per-sublayer lookup, then re-buckets.
    """
    point, non_point, geometry_mismatch, unknown_geometry = [], [], [], []
    for m in matched:
        a_geom = m["authoritative_geometry_type"]
        c_geom = m["captured_geometry_type"]
        if a_geom is None or c_geom is None:
            unknown_geometry.append(m)
        elif a_geom == POINT and c_geom == POINT:
            point.append(m)
        elif a_geom == c_geom:
            non_point.append(m)
        else:
            geometry_mismatch.append(m)
    return {
        "point": point,
        "non_point": non_point,
        "geometry_mismatch": geometry_mismatch,
        "unknown_geometry": unknown_geometry,
    }


def build_layer_entry(match: dict, default_threshold_m: float, copy_attachments: bool) -> dict:
    """Build a new config.json layer entry for a point match.

    Insertion order matches config.json's existing convention
    (``authoritative_url``, ``captured_url``, ``match_threshold_m``,
    ``field_map``, ``copy_attachments``) so newly-added entries diff cleanly
    next to refreshed ones. ``type_field_*`` keys are omitted (optional; most
    layers have no type attribute to match on).
    """
    entry = {
        "authoritative_url": match["authoritative_url"],
        "captured_url": match["captured_url"],
        "match_threshold_m": default_threshold_m,
        "field_map": {},
        "copy_attachments": copy_attachments,
    }
    validate_layer_config(entry)
    return entry


def partition_point_matches(existing_layers: dict, point_matches: list[dict]) -> dict:
    """Classify point matches by how they relate to existing config.json keys,
    without building or merging any entries.

    This is the read-only half of ``merge_layers_config``'s classification,
    pulled out so a caller that must decide something *before* the actual
    merge -- e.g. ``--auto-configure`` deciding which layers are worth
    fetching full feature sets for, to calibrate a per-layer threshold --
    can never classify a match differently than ``merge_layers_config``
    itself will (which calls this function internally).

    Matching against existing keys is case-insensitive, per the same
    AGOL-casing-vs-hand-written-key rationale as ``merge_layers_config``.

    Returns ``{"new": [...], "changed": [...], "unchanged": [...]}``, each a
    list of the original match dicts. Every entry in ``"changed"`` and
    ``"unchanged"`` additionally carries ``"_existing_key"``, the existing
    config.json key it corresponds to (preserving that key's original
    casing) -- entries in ``"new"`` have no existing key, so don't carry it.

    Raises ``ValueError`` if ``existing_layers`` has two or more keys that
    collide case-insensitively (e.g. ``Water_Hydrants`` and
    ``water_hydrants``) -- see ``merge_layers_config``'s docstring for why.
    """
    existing_by_lower = {}
    for key in existing_layers:
        lower = key.lower()
        if lower in existing_by_lower:
            raise ValueError(
                "config.json has keys that collide case-insensitively: "
                f"{existing_by_lower[lower]!r} and {key!r}. Rename one before "
                "running --auto-configure."
            )
        existing_by_lower[lower] = key

    new, changed, unchanged = [], [], []
    for match in point_matches:
        existing_key = existing_by_lower.get(match["name"].lower())
        if existing_key is None:
            new.append(match)
            continue

        existing = existing_layers[existing_key]
        if (
            existing.get("authoritative_url") == match["authoritative_url"]
            and existing.get("captured_url") == match["captured_url"]
        ):
            unchanged.append({**match, "_existing_key": existing_key})
        else:
            changed.append({**match, "_existing_key": existing_key})

    return {"new": new, "changed": changed, "unchanged": unchanged}


def merge_layers_config(
    existing_layers: dict, point_matches: list[dict],
    default_threshold_m: float, copy_attachments: bool,
) -> dict:
    """Merge point matches into an existing ``layers`` config dict.

    Matching against existing keys is **case-insensitive**: a live sublayer
    named ``Water_Hydrants`` matches an existing key ``water_hydrants``. This
    matters because AGOL ``name`` casing varies by org while hand-written
    config keys are commonly lowercase snake_case -- without it, a re-run
    would orphan existing entries (and their hand-curated ``type_field_*``
    pairs) and add PascalCase duplicates instead of refreshing in place.
    Classification into new/changed/unchanged is delegated to
    ``partition_point_matches`` (see its docstring).

    For each point match (``name`` = authoritative side's verbatim name):
      * no existing key matches case-insensitively -> insert
        ``build_layer_entry(...)`` under the verbatim ``name``, bucket
        ``added``. Its ``match_threshold_m`` is the match's
        ``"resolved_threshold_m"`` if present (rounded to 2 decimals,
        matching config.json's existing precision style), else
        ``default_threshold_m``.
      * existing key matches, both URLs already equal -> bucket
        ``unchanged`` (reported under the existing key). Never touched --
        not even to check for a ``"resolved_threshold_m"`` -- so a fully
        no-op ``--auto-configure`` re-run stays a fully no-op re-run.
      * existing key matches, a URL differs -> refresh
        ``authoritative_url``/``captured_url``, bucket ``updated`` (reported
        under the existing key) with old->new per changed field. Every other
        field is left byte-for-byte untouched (including the existing key's
        casing, key order, and any ``type_field_*`` pair) *unless* the match
        carries a ``"resolved_threshold_m"`` that differs from the existing
        ``match_threshold_m`` -- the one deliberate exception to "refresh
        touches only URLs" -- in which case ``match_threshold_m`` is also
        refreshed and reported in ``changes``. A ``"changed"`` match with no
        ``"resolved_threshold_m"`` key preserves the existing threshold
        exactly as before.

    Existing keys with no matching name are left alone and appear in no
    bucket. Auto-configure only ever adds or refreshes -- it never deletes,
    and it never renames an existing key (the verbatim AGOL name is used only
    for genuinely-new entries).

    Raises ``ValueError`` under the same condition as ``partition_point_matches``.

    Returns ``{"layers": <merged dict>, "added": [verbatim_name, ...],
    "updated": [{"name": <existing_key>, "changes": {...}}, ...],
    "unchanged": [<existing_key>, ...]}``.
    """
    merged = {k: dict(v) for k, v in existing_layers.items()}
    partition = partition_point_matches(existing_layers, point_matches)
    added, updated, unchanged = [], [], []

    for match in partition["new"]:
        name = match["name"]
        threshold = (
            round(match["resolved_threshold_m"], 2)
            if "resolved_threshold_m" in match
            else default_threshold_m
        )
        merged[name] = build_layer_entry(match, threshold, copy_attachments)
        added.append(name)

    for match in partition["changed"]:
        existing_key = match["_existing_key"]
        new_auth = match["authoritative_url"]
        new_cap = match["captured_url"]

        existing = merged[existing_key]
        old_auth = existing.get("authoritative_url")
        old_cap = existing.get("captured_url")
        old_threshold = existing.get("match_threshold_m")

        new_threshold = None
        if "resolved_threshold_m" in match:
            candidate = round(match["resolved_threshold_m"], 2)
            if candidate != old_threshold:
                new_threshold = candidate

        # Refresh the URL fields (and, if resolved, the threshold); preserve
        # every other field, the existing key's casing, and key order.
        # Rebuild the dict so refreshed fields keep their original positions
        # rather than being re-appended at the end.
        refreshed = {}
        for key, value in existing.items():
            if key == "authoritative_url":
                refreshed[key] = new_auth
            elif key == "captured_url":
                refreshed[key] = new_cap
            elif key == "match_threshold_m" and new_threshold is not None:
                refreshed[key] = new_threshold
            else:
                refreshed[key] = value
        # A layer entry is required to carry both URL keys (validate_layer_config),
        # so the loop above always set them -- but guard anyway in case a
        # hand-edited entry is missing one.
        refreshed.setdefault("authoritative_url", new_auth)
        refreshed.setdefault("captured_url", new_cap)
        merged[existing_key] = refreshed

        changes = {}
        if old_auth != new_auth:
            changes["authoritative_url"] = [old_auth, new_auth]
        if old_cap != new_cap:
            changes["captured_url"] = [old_cap, new_cap]
        if new_threshold is not None:
            changes["match_threshold_m"] = [old_threshold, new_threshold]
        updated.append({"name": existing_key, "changes": changes})

    for match in partition["unchanged"]:
        unchanged.append(match["_existing_key"])

    return {"layers": merged, "added": added, "updated": updated, "unchanged": unchanged}