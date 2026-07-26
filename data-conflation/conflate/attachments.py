"""
Attachment copy functionality for feature layer conflation.
"""
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def copy_attachments(source_layer, source_oid, target_layer, target_oid, already_copied=None):
    """
    Copy file attachments from a source feature to a target feature.

    Supports being called multiple times on the same feature pair without re-uploading
    attachments that have already been successfully copied.

    Args:
        source_layer: arcgis.features.FeatureLayer-like object with .attachments sub-object
        source_oid: Object ID of the source feature
        target_layer: arcgis.features.FeatureLayer-like object with .attachments sub-object
        target_oid: Object ID of the target feature
        already_copied: Set of attachment filenames (strings) already successfully copied
                       in a prior call for this feature pair. Defaults to None (empty set).

    Returns:
        Tuple of (status_string, updated_set) where:
        - status_string: String like "2/3" indicating count of copied attachments vs total
        - updated_set: Updated set of all attachment names now on the target (old + new)
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
        return (f"0/{0}", already_copied)

    total_attachments = len(source_attachments)
    if total_attachments == 0:
        return ("0/0", already_copied)

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        successfully_copied = set()

        for attachment in source_attachments:
            attachment_name = attachment.get("name")
            attachment_id = attachment.get("id")

            if not attachment_name:
                logger.warning(f"Attachment missing 'name' key: {attachment}")
                continue

            if not attachment_id:
                logger.warning(f"Attachment missing 'id' key: {attachment}")
                continue

            # Skip if already copied in a prior call
            if attachment_name in already_copied:
                logger.debug(f"Skipping already-copied attachment: {attachment_name}")
                successfully_copied.add(attachment_name)
                continue

            # Download attachment from source
            downloaded_path = None
            try:
                downloaded_path = source_layer.attachments.download(
                    source_oid,
                    attachment_id,
                    save_path=str(temp_path)
                )
                logger.debug(f"Downloaded attachment {attachment_name} to {downloaded_path}")
            except Exception as e:
                logger.error(
                    f"Failed to download attachment {attachment_name} "
                    f"(ID {attachment_id}) from source OID {source_oid}: {e}"
                )
                continue

            # Upload attachment to target
            try:
                target_layer.attachments.add(target_oid, downloaded_path)
                logger.debug(f"Uploaded attachment {attachment_name} to target OID {target_oid}")
                successfully_copied.add(attachment_name)
            except Exception as e:
                logger.error(
                    f"Failed to upload attachment {attachment_name} "
                    f"to target OID {target_oid}: {e}"
                )
                continue

        # Build updated set of all successfully copied attachments
        updated_copied = already_copied | successfully_copied

        # Count total attachments now confirmed on target
        # This includes both newly copied and previously copied ones
        final_count = len(updated_copied)
        status_string = f"{final_count}/{total_attachments}"

        return (status_string, updated_copied)

    finally:
        # Always clean up the temporary directory
        if temp_dir:
            import shutil
            try:
                shutil.rmtree(temp_dir)
                logger.debug(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up temporary directory {temp_dir}: {e}")
