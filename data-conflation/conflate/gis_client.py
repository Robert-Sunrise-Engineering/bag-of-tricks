"""GIS client module for connecting to ArcGIS Online and validating feature layers."""

from arcgis.gis import GIS
from arcgis.features import FeatureLayer


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


def validate_schema(layer: FeatureLayer, required_fields: list[str]) -> None:
    """
    Validate that a feature layer has all required fields.

    Args:
        layer: FeatureLayer to validate
        required_fields: List of required field names

    Raises:
        ValueError: If any required fields are missing from the layer
    """
    # Get field names from the layer, handling both dict and object access patterns
    layer_field_names = []
    for field in layer.properties.fields:
        if isinstance(field, dict):
            layer_field_names.append(field["name"])
        else:
            layer_field_names.append(field.name)

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
