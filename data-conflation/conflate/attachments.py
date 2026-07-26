"""
Attachment copy functionality for feature layer conflation.
"""
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def target_attachment_name(captured_global_id, attachment_id, original_name):
    """Build a collision-proof target-side filename for a copied attachment.

    Keyed on the captured feature's GlobalID (the durable identity used
    everywhere else in this codebase: the ledger, the report, validate_schema's
    required fields — unlike OBJECTID, which can shift if a layer is
    republished) plus the source attachment's own ID, so a generic/default
    filename (e.g. a device default like "Photo1.jpg") can never collide with
    an unrelated attachment already on the target, another captured feature's
    attachment, or a different attachment on the same captured feature that
    happens to share a name. Deterministic: re-running the copy for the same
    source attachment always produces the same target name, which is what
    makes name-based retry-dedup safe.
    """
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix
    clean_global_id = captured_global_id.strip("{}").replace("-", "")
    return f"{clean_global_id}_att{attachment_id}_{stem}{suffix}"


def copy_attachments(
    source_layer, source_oid, target_layer, target_oid, captured_global_id, already_copied=None
):
    """
    Copy file attachments from a source feature to a target feature.

    Supports being called multiple times on the same feature pair without re-uploading
    attachments that have already been successfully copied.

    Uploaded attachments are renamed on the target side to a collision-proof
    name (see target_attachment_name) derived from captured_global_id and the
    source attachment's own ID, rather than kept under their original
    (possibly generic/non-unique) filename.

    Args:
        source_layer: arcgis.features.FeatureLayer-like object with .attachments sub-object
        source_oid: Object ID of the source feature
        target_layer: arcgis.features.FeatureLayer-like object with .attachments sub-object
        target_oid: Object ID of the target feature
        captured_global_id: GlobalID of the captured (source) feature, used to build
                       collision-proof target attachment names.
        already_copied: Set of target-side attachment names (as produced by
                       target_attachment_name) already successfully copied in a
                       prior call for this feature pair. Defaults to None (empty set).

    Returns:
        Tuple of (status_string, names_set, ids_list) where:
        - status_string: String like "2/3" indicating count of copied attachments vs total
        - names_set: Set of all target-side attachment names now on the target (old + new)
        - ids_list: List of target-layer attachment IDs newly uploaded in THIS call
                    (for rollback cleanup). Does not include already-copied attachments,
                    since no target ID is known for those without an upload happening.
    """
    if already_copied is None:
        already_copied = set()
    else:
        already_copied = set(already_copied)  # Ensure we work with a copy

    # Get list of source attachments
    try:
        source_attachments = source_layer.attachments.get_list(source_oid)
    except Exception as e:
        logger.error(f"Failed to list attachments for source OID {source_oid}: {e}")
        return (f"0/{0}", already_copied, [])

    total_attachments = len(source_attachments)
    if total_attachments == 0:
        return ("0/0", already_copied, [])

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        successfully_copied_names = set()
        successfully_copied_ids = []

        for attachment in source_attachments:
            attachment_name = attachment.get("name")
            attachment_id = attachment.get("id")

            if not attachment_name:
                logger.warning(f"Attachment missing 'name' key: {attachment}")
                continue

            if not attachment_id:
                logger.warning(f"Attachment missing 'id' key: {attachment}")
                continue

            target_name = target_attachment_name(captured_global_id, attachment_id, attachment_name)

            # Skip if already copied in a prior call
            if target_name in already_copied:
                logger.debug(f"Skipping already-copied attachment: {target_name}")
                successfully_copied_names.add(target_name)
                continue

            # Download attachment from source
            downloaded_path = None
            try:
                downloaded_list = source_layer.attachments.download(
                    source_oid,
                    attachment_id,
                    save_path=str(temp_path)
                )
                # download() returns a list of paths; take the first element
                if isinstance(downloaded_list, list):
                    downloaded_path = downloaded_list[0] if downloaded_list else None
                else:
                    downloaded_path = downloaded_list

                if downloaded_path is None:
                    logger.error(
                        f"Failed to download attachment {attachment_name} "
                        f"(ID {attachment_id}) from source OID {source_oid}: "
                        f"download() returned empty list or None"
                    )
                    continue

                logger.debug(f"Downloaded attachment {attachment_name} to {downloaded_path}")
            except Exception as e:
                logger.error(
                    f"Failed to download attachment {attachment_name} "
                    f"(ID {attachment_id}) from source OID {source_oid}: {e}"
                )
                continue

            # Rename the downloaded file to the collision-proof target name
            # before uploading, since attachments.add() takes the uploaded
            # file's own basename as the target attachment's display name.
            try:
                renamed_path = Path(downloaded_path).with_name(target_name)
                Path(downloaded_path).rename(renamed_path)
            except Exception as e:
                logger.error(
                    f"Failed to rename downloaded attachment {attachment_name} "
                    f"(ID {attachment_id}) to {target_name}: {e}"
                )
                continue

            # Upload attachment to target
            try:
                add_response = target_layer.attachments.add(target_oid, str(renamed_path))
                add_result = (add_response or {}).get("addAttachmentResult", {})
                if not add_result.get("success"):
                    logger.error(
                        f"Upload reported failure for {target_name} "
                        f"(ID {attachment_id}) to target OID {target_oid}: {add_response}"
                    )
                    continue
                logger.debug(f"Uploaded attachment {target_name} to target OID {target_oid}")
                successfully_copied_names.add(target_name)
                new_attachment_id = add_result.get("objectId")
                if new_attachment_id is not None:
                    successfully_copied_ids.append(new_attachment_id)
            except Exception as e:
                logger.error(
                    f"Failed to upload attachment {target_name} "
                    f"(ID {attachment_id}) to target OID {target_oid}: {e}"
                )
                continue

        # Build updated set of all successfully copied attachments
        updated_copied = already_copied | successfully_copied_names

        # Count total attachments now confirmed on target
        # This includes both newly copied and previously copied ones
        final_count = len(updated_copied)
        status_string = f"{final_count}/{total_attachments}"

        return (status_string, updated_copied, successfully_copied_ids)

    finally:
        # Always clean up the temporary directory
        if temp_dir:
            import shutil
            try:
                shutil.rmtree(temp_dir)
                logger.debug(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up temporary directory {temp_dir}: {e}")


def delete_attachments(target_layer, target_oid, attachment_ids_to_delete):
    """
    Delete attachments from a feature by their IDs.

    Args:
        target_layer: arcgis.features.FeatureLayer with attachment support
        target_oid: Object ID of the feature to remove attachments from
        attachment_ids_to_delete: List of attachment data IDs to delete

    Returns:
        Dict with 'success' (bool) and 'errors' (list of str) keys
    """
    result = {"success": True, "errors": []}

    try:
        # Use AGOL's attachments API to delete each attachment
        for attachment_id in attachment_ids_to_delete:
            try:
                target_layer.attachments.delete(target_oid, attachment_id)
                logger.debug(f"Deleted attachment with ID {attachment_id} from OID {target_oid}")
            except Exception as e:
                result["success"] = False
                error_msg = f"Failed to delete attachment ID {attachment_id}: {e}"
                logger.error(error_msg)
                result["errors"].append(error_msg)
    except Exception as e:
        result["success"] = False
        logger.error(f"Error deleting attachments from OID {target_oid}: {e}")
        result["errors"].append(f"General error: {e}")

    return result


def delete_attachments_batch(layer, oid_to_ids):
    """
    Batch delete attachments from multiple features.

    Args:
        layer: arcgis.features.FeatureLayer with attachment support
        oid_to_ids: Dict mapping authoritative_oid -> [list of attachment_ids]

    Returns:
        List of result dicts (one per OID), each shaped as returned by delete_attachments()
    """
    results = []

    for authoritative_oid, attachment_ids in oid_to_ids.items():
        if not attachment_ids:
            continue

        result = delete_attachments(layer, authoritative_oid, attachment_ids)
        result["authoritative_oid"] = authoritative_oid
        results.append(result)

    return results
