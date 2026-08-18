"""GIS client module for connecting to ArcGIS Online and validating feature layers."""

import logging
import re

from arcgis.gis import GIS
from arcgis.features import FeatureLayer, FeatureLayerCollection

logger = logging.getLogger(__name__)

# Matches a feature-service sublayer URL: a trailing `/<digits>` with an
# optional slash, e.g. `.../FeatureServer/0` or `.../FeatureServer/0/`. A
# service *root* URL ends at `.../FeatureServer` and must not match this.
_SUBLAYER_URL_RE = re.compile(r"/\d+/?$")


def connect(local_config: dict) -> GIS:
    """
    Connect to an ArcGIS portal and return a GIS object.

    Dispatches on local_config["auth_type"] (default "builtin"):
      * "builtin": plain AGOL username/password login. Only works for
        AGOL built-in accounts.
      * "oauth": browser-based OAuth login via a registered application's
        client_id, required for orgs whose logins are federated through an
        identity provider (e.g. ADFS/SAML) — those orgs reject a directly
        POSTed password even when it's correct. Combined with `profile`,
        the arcgis library does the interactive browser login only the
        first time a given profile name is used; every call after that
        (including unattended/automated ones on the same machine/user
        account) silently reuses the cached refresh token, and access-token
        renewal is handled internally with no user interaction.

    Args:
        local_config: parsed config.local.json dict. Required keys depend
            on auth_type: "portal_url" always; "username"/"password" for
            builtin; "client_id"/"profile" for oauth.

    Returns:
        GIS object authenticated to the portal

    Raises:
        ValueError: If auth_type == "oauth" and client_id/profile are missing.
        Exceptions from arcgis library (e.g., authentication failures). For
        oauth, a cached-profile failure is re-raised with a message pointing
        at the likely cause (expired refresh token) since an automated run
        has no one present to complete an ADFS login page.
    """
    portal_url = local_config["portal_url"]
    auth_type = local_config.get("auth_type", "builtin")

    if auth_type == "builtin":
        return GIS(portal_url, local_config["username"], local_config["password"])

    if auth_type == "oauth":
        client_id = local_config.get("client_id")
        profile = local_config.get("profile")
        if not client_id or not profile:
            raise ValueError(
                "auth_type 'oauth' requires both 'client_id' and 'profile' "
                "in config.local.json."
            )
        try:
            return GIS(portal_url, client_id=client_id, profile=profile)
        except Exception as e:
            raise Exception(
                f"OAuth login failed for profile '{profile}'. If this is an "
                "automated/unattended run, the cached refresh token has "
                "likely expired and no one was present to complete the "
                "identity-provider login page. Run this tool manually once "
                "to re-authenticate interactively and refresh the cached "
                f"profile. Original error: {e}"
            ) from e

    raise ValueError(f"Unknown auth_type: {auth_type!r}")


def get_layer(gis: GIS, url: str) -> FeatureLayer:
    """
    Get a FeatureLayer from a URL.

    Args:
        gis: Authenticated GIS object
        url: URL of the feature layer

    Returns:
        FeatureLayer object
    """
    return FeatureLayer(url, gis=gis)


def _prop(entry, key, default=None):
    """Read ``key`` from an entry that may be a plain dict or an
    attribute-access object, i.e. ``entry[key]`` vs. ``entry.key``.

    The arcgis library returns field/layer summary entries as plain dicts in
    some contexts and as attribute-access objects in others (SDK/server
    version dependent). Used by both ``validate_schema`` (field entries) and
    ``list_service_sublayers`` (sublayer summary entries) so this shape
    handling lives in one place.
    """
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


def validate_schema(layer: FeatureLayer, required_fields: list[str]) -> None:
    """
    Validate that a feature layer has all required fields.

    Args:
        layer: FeatureLayer to validate
        required_fields: List of required field names

    Raises:
        ValueError: If any required fields are missing from the layer
    """
    # Get field names from the layer, handling both dict and object access
    # patterns (see _prop below).
    layer_field_names = [_prop(field, "name") for field in layer.properties.fields]

    # Find missing fields
    missing_fields = [f for f in required_fields if f not in layer_field_names]

    if missing_fields:
        raise ValueError(
            f"Layer is missing required fields: {', '.join(missing_fields)}"
        )


def validate_capabilities(layer: FeatureLayer, copy_attachments: bool) -> None:
    """
    Validate that a feature layer has required capabilities.

    Args:
        layer: FeatureLayer to validate
        copy_attachments: Whether attachments are required to be enabled

    Raises:
        ValueError: If required capabilities are missing or attachments are
                   required but not enabled on the layer
    """
    # Check attachments first
    if copy_attachments:
        has_attachments = layer.properties.get("hasAttachments", False)
        if not has_attachments:
            raise ValueError(
                "Layer does not have attachments enabled"
            )

    # Check edit capabilities
    capabilities_str = layer.properties.get("capabilities", "")
    missing_capabilities = []

    if "Create" not in capabilities_str:
        missing_capabilities.append("Create")
    if "Update" not in capabilities_str:
        missing_capabilities.append("Update")

    if missing_capabilities:
        raise ValueError(
            f"Layer is missing required capabilities: {', '.join(missing_capabilities)}"
        )


def validate_geometry_type(layer: FeatureLayer) -> None:
    """
    Validate that a feature layer's geometry type is point.

    Matching (geodesic_distance on a feature's x/y) only makes sense for
    point features. A line/polygon layer has no single x/y and would
    silently produce None lon/lat (see features.simplify_feature), crashing
    later deep inside geodesic_distance with an opaque error. Checking this
    once at startup gives a clear, immediate error instead.

    Args:
        layer: FeatureLayer to validate

    Raises:
        ValueError: If the layer's geometry type is not esriGeometryPoint
    """
    geometry_type = layer.properties.get("geometryType", "")
    if geometry_type != "esriGeometryPoint":
        raise ValueError(
            f"Layer geometry type is {geometry_type!r}, but this tool only "
            "supports point layers (esriGeometryPoint)."
        )


def list_service_sublayers(gis: GIS, service_url: str) -> list[dict]:
    """
    Enumerate the spatial sublayers of a feature-service root URL.

    Reads the lightweight root-service JSON (``FeatureLayerCollection.properties.layers``)
    which already carries each sublayer's ``id``/``name``/``geometryType`` in a
    single request -- it does not touch ``flc.layers`` (which lazily fetches
    each sublayer) and excludes ``flc.properties.tables`` (non-spatial tables)
    entirely. Generated sublayer URLs are built from the caller's own root URL
    (not a resolved SDK URL) so they stay recognizable in config.json.

    Args:
        gis: Authenticated GIS object.
        service_url: A feature-service *root* URL (ending at FeatureServer,
            with no trailing ``/<id>`` sublayer index).

    Returns:
        One dict per spatial sublayer: ``{"id": int, "name": str,
        "geometry_type": str | None, "url": str}``, in the order the service
        lists them. ``geometry_type`` is ``None`` when the root summary didn't
        include ``geometryType`` for that entry (server-version dependent);
        no extra request is made to resolve it here -- the caller decides.

    Raises:
        ValueError: If ``service_url`` looks like a sublayer URL already
            (trailing ``/<id>``) -- the exact hand-bookkeeping mistake this
            function exists to make unnecessary.
    """
    if _SUBLAYER_URL_RE.search(service_url):
        raise ValueError(
            f"service_url {service_url!r} looks like a sublayer URL (trailing "
            f"/<id>). Pass the feature-service root URL (ending at "
            f"FeatureServer, no index) instead."
        )

    flc = FeatureLayerCollection(service_url, gis=gis)

    table_ids = {_prop(t, "id") for t in (flc.properties.get("tables") or [])}
    sublayers = []
    for entry in flc.properties.get("layers") or []:
        layer_id = _prop(entry, "id")
        if layer_id is None:
            # A sublayer without an id can't form a valid sublayer URL -- it
            # would build '.../FeatureServer/None' and silently corrupt
            # config.json. Skip it (with a warning) rather than emit a bad
            # entry. (The geometry_type=None case below is allowed because the
            # caller resolves it via a per-sublayer lookup; a None id has no
            # such recourse.)
            logger.warning(
                "Skipping sublayer entry with no id in service summary: %r",
                _prop(entry, "name"),
            )
            continue
        if layer_id in table_ids:
            # Defensive: tables normally live under .tables, not .layers, but
            # don't emit a non-spatial table into a config bucket if a server
            # ever lists it under both.
            continue
        name = _prop(entry, "name")
        if name is None:
            # An unnamed sublayer can't be matched by name (match_sublayers
            # does name.lower()) -- skip it rather than let a None name
            # propagate and AttributeError-abort the whole --auto-configure run.
            logger.warning(
                "Skipping sublayer entry (id %r) with no name in service "
                "summary: it can't be matched by name.", layer_id,
            )
            continue
        geometry_type = _prop(entry, "geometryType")
        sublayers.append(
            {
                "id": layer_id,
                "name": name,
                "geometry_type": geometry_type,
                "url": f"{service_url.rstrip('/')}/{layer_id}",
            }
        )
    return sublayers
