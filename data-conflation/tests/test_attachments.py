"""
Unit tests for conflate.attachments.copy_attachments and target_attachment_name.

Tests the critical dedup-retry behavior, collision-proof naming, and error
handling without requiring real AGOL connections. Uses mocks for all layer
operations.
"""
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from conflate.attachments import copy_attachments, target_attachment_name

CAPTURED_GLOBAL_ID = "{6cd37221-d36b-47cd-8078-5dc0cee3979f}"


def _expected_name(attachment_id, original_name, global_id=CAPTURED_GLOBAL_ID):
    return target_attachment_name(global_id, attachment_id, original_name)


@pytest.fixture
def mock_source_layer():
    """Create a mock source layer with realistic attachment download behavior."""
    layer = MagicMock()

    # Store temp files created by download to clean up later
    created_files = []

    def download_side_effect(oid, attachment_id, save_path):
        """
        Simulate downloading an attachment by creating a dummy file.
        Returns the path to the created file.
        """
        # Create a temp file in the provided save_path directory
        temp_path = Path(save_path)
        temp_path.mkdir(parents=True, exist_ok=True)

        # Create a dummy file with a predictable name
        file_path = temp_path / f"attachment_{attachment_id}.dat"
        file_path.write_text(f"Dummy attachment content for ID {attachment_id}")
        created_files.append(file_path)
        # attachment.download() returns a list of paths, even for single downloads
        return [str(file_path)]

    layer.attachments.get_list.return_value = [
        {"name": "a.jpg", "id": 1},
        {"name": "b.jpg", "id": 2},
        {"name": "c.jpg", "id": 3},
    ]
    layer.attachments.download.side_effect = download_side_effect

    return layer


@pytest.fixture
def mock_target_layer():
    """Create a mock target layer whose attachments.add() returns a realistic
    AGOL addAttachment response, with target-side objectIds distinct from the
    source-side attachment ids (offset by 900).

    copy_attachments renames the downloaded file to its collision-proof target
    name (e.g. "6cd37221.../att1_a.jpg") before uploading, so the source
    attachment id is recovered from the "_att<id>_" segment of the uploaded
    file's name rather than from the old "attachment_<id>.dat" pattern.
    """
    layer = MagicMock()

    def add_side_effect(oid, file_path):
        match = re.search(r"_att(\d+)_", Path(file_path).name)
        source_id = int(match.group(1))
        return {"addAttachmentResult": {"objectId": 900 + source_id, "success": True}}

    layer.attachments.add = MagicMock(side_effect=add_side_effect)
    return layer


class TestTargetAttachmentName:
    """Tests for the collision-proof naming helper itself."""

    def test_basic_format(self):
        name = target_attachment_name("abc123", 7, "Photo1.jpg")
        assert name == "abc123_att7_Photo1.jpg"

    def test_strips_braces_and_hyphens_from_global_id(self):
        name = target_attachment_name("{6cd37221-d36b-47cd-8078-5dc0cee3979f}", 1, "a.jpg")
        assert name == "6cd37221d36b47cd80785dc0cee3979f_att1_a.jpg"

    def test_different_global_ids_avoid_collision_on_same_original_name(self):
        """
        Two different captured features whose attachments share the exact same
        generic filename (e.g. a device default like "Photo1.jpg") must map to
        different target names -- this is the collision this feature exists to
        prevent.
        """
        name_a = target_attachment_name("feature-aaa", 1, "Photo1.jpg")
        name_b = target_attachment_name("feature-bbb", 1, "Photo1.jpg")
        assert name_a != name_b


class TestCopyAttachmentsBasic:
    """Basic test: first call with all new attachments."""

    def test_first_call_copies_all_attachments(self, mock_source_layer, mock_target_layer):
        """
        First call should copy all 3 attachments.

        Verifies:
        - target_layer.attachments.add is called exactly 3 times
        - status string is "3/3"
        - returned set contains the collision-proof target names for all three
        - returned ids list contains the TARGET-side attachment ids (not the
          source-side ids)
        """
        source_oid = 100
        target_oid = 200

        status, updated_set, copied_ids = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            CAPTURED_GLOBAL_ID,
            already_copied=None
        )

        # Verify add was called exactly 3 times
        assert mock_target_layer.attachments.add.call_count == 3

        # Verify status string
        assert status == "3/3"

        # Verify the updated set contains the collision-proof target names
        assert updated_set == {
            _expected_name(1, "a.jpg"),
            _expected_name(2, "b.jpg"),
            _expected_name(3, "c.jpg"),
        }

        # Verify the returned ids are the target-side ids from add()'s
        # response (900 + source id), not the source-side ids (1, 2, 3)
        assert sorted(copied_ids) == [901, 902, 903]


class TestCopyAttachmentsDedupRetry:
    """Test the critical dedup-retry behavior: second call should not re-upload."""

    def test_second_call_skips_already_copied(self, mock_source_layer, mock_target_layer):
        """
        Second call with already_copied set should skip those attachments.

        Verifies:
        - target_layer.attachments.add is called 0 times in the second call
        - status string reflects only what's already copied
        - no re-upload happens (critical dedup guarantee)
        """
        source_oid = 100
        target_oid = 200

        # First call
        status1, already_copied_set, copied_ids1 = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            CAPTURED_GLOBAL_ID,
            already_copied=None
        )

        # Verify first call worked
        assert status1 == "3/3"
        assert already_copied_set == {
            _expected_name(1, "a.jpg"),
            _expected_name(2, "b.jpg"),
            _expected_name(3, "c.jpg"),
        }
        assert mock_target_layer.attachments.add.call_count == 3
        assert sorted(copied_ids1) == [901, 902, 903]

        # Reset the mock to clear the first call's count
        mock_target_layer.attachments.add.reset_mock()

        # Second call with the already_copied set
        status2, updated_set, copied_ids2 = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            CAPTURED_GLOBAL_ID,
            already_copied=already_copied_set
        )

        # CRITICAL: Verify add is NOT called again in the second call
        assert mock_target_layer.attachments.add.call_count == 0, \
            "Second call with already_copied set should not re-upload any attachments"

        # Verify status still reflects all attachments are copied
        assert status2 == "3/3"

        # Verify the updated set is unchanged
        assert updated_set == already_copied_set

        # No new uploads happened, so no new target-side ids are reported
        assert copied_ids2 == []


class TestCopyAttachmentsPartialFailure:
    """Test error handling: if one download fails, others should continue."""

    def test_partial_failure_continues_processing(self, mock_source_layer, mock_target_layer):
        """
        If download fails for one attachment, others should still be processed.

        Verifies:
        - Function does not raise an exception
        - Returns status like "2/3" (successfully copied 2 out of 3)
        - Successfully copied attachments are in the returned set
        - Failed attachment is NOT in the returned set
        - target_layer.attachments.add is called only for successful downloads (2 times)
        """
        source_oid = 100
        target_oid = 200

        # Create a download side effect that fails for attachment_id == 2
        def download_with_failure(oid, attachment_id, save_path):
            if attachment_id == 2:
                raise Exception("Simulated download failure for attachment 2")

            # Otherwise, create a dummy file
            temp_path = Path(save_path)
            temp_path.mkdir(parents=True, exist_ok=True)
            file_path = temp_path / f"attachment_{attachment_id}.dat"
            file_path.write_text(f"Dummy attachment content for ID {attachment_id}")
            return str(file_path)

        mock_source_layer.attachments.download.side_effect = download_with_failure

        # Call should not raise, even though one attachment fails
        status, updated_set, copied_ids = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            CAPTURED_GLOBAL_ID,
            already_copied=None
        )

        # Verify it doesn't raise and continues processing
        # add should be called only for successful downloads (2 times: for IDs 1 and 3)
        assert mock_target_layer.attachments.add.call_count == 2

        # Status should be "2/3" (2 successfully copied out of 3 total)
        assert status == "2/3"

        # Only successfully copied attachments should be in the set
        assert updated_set == {_expected_name(1, "a.jpg"), _expected_name(3, "c.jpg")}

        # Only target-side ids for the successful uploads (source ids 1, 3 -> 901, 903)
        assert sorted(copied_ids) == [901, 903]


class TestCopyAttachmentsEmptySource:
    """Test behavior with empty source attachments."""

    def test_empty_source_returns_zero_zero(self, mock_source_layer, mock_target_layer):
        """
        Source with no attachments should return "0/0" and empty set.
        """
        source_oid = 100
        target_oid = 200

        # Override to return empty list
        mock_source_layer.attachments.get_list.return_value = []

        status, updated_set, copied_ids = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            CAPTURED_GLOBAL_ID,
            already_copied=None
        )

        # No attachments means "0/0"
        assert status == "0/0"

        # Updated set should be empty
        assert updated_set == set()
        assert copied_ids == []

        # add should never be called
        assert mock_target_layer.attachments.add.call_count == 0


class TestCopyAttachmentsGetListFailure:
    """Test behavior when getting attachment list fails."""

    def test_get_list_failure_returns_zero_zero(self, mock_source_layer, mock_target_layer):
        """
        If get_list fails, should return "0/0" and preserve already_copied.
        """
        source_oid = 100
        target_oid = 200
        already_copied = {"old.jpg"}

        # Make get_list raise an exception
        mock_source_layer.attachments.get_list.side_effect = Exception("Network error")

        status, updated_set, copied_ids = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            CAPTURED_GLOBAL_ID,
            already_copied=already_copied
        )

        # Function should not raise; should return "0/0"
        assert status == "0/0"

        # already_copied should be preserved
        assert updated_set == {"old.jpg"}
        assert copied_ids == []


class TestCopyAttachmentsUploadFailure:
    """Test behavior when uploading fails."""

    def test_upload_failure_continues_processing(self, mock_source_layer, mock_target_layer):
        """
        If the AGOL response for one upload reports success=False, others
        should still be processed.

        Verifies:
        - Function does not raise
        - Returns status like "2/3" (2 successful uploads out of 3 total)
        - Successfully uploaded attachments are in the returned set, with
          their target-side ids
        - Failed attachment is NOT in the returned set or ids list
        """
        source_oid = 100
        target_oid = 200

        # Make add report failure for the second call (attachment id 2 / b.jpg)
        def add_with_failure(oid, file_path):
            match = re.search(r"_att(\d+)_", Path(file_path).name)
            source_id = int(match.group(1))
            if source_id == 2:
                return {"addAttachmentResult": {"success": False, "error": "simulated failure"}}
            return {"addAttachmentResult": {"objectId": 900 + source_id, "success": True}}

        mock_target_layer.attachments.add.side_effect = add_with_failure

        status, updated_set, copied_ids = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            CAPTURED_GLOBAL_ID,
            already_copied=None
        )

        # Function should not raise
        # add should be called 3 times (one reports failure but others succeed)
        assert mock_target_layer.attachments.add.call_count == 3

        # Status should reflect only successful uploads: "2/3"
        assert status == "2/3"

        # Only successfully uploaded attachments should be in the set
        # (a.jpg and c.jpg succeed, b.jpg fails)
        assert updated_set == {_expected_name(1, "a.jpg"), _expected_name(3, "c.jpg")}
        assert sorted(copied_ids) == [901, 903]


class TestCopyAttachmentsMissingFields:
    """Test behavior when attachment dict is missing name or id fields."""

    def test_missing_name_field_skipped(self, mock_source_layer, mock_target_layer):
        """
        Attachment without 'name' should be skipped but not cause failure.
        """
        source_oid = 100
        target_oid = 200

        # Override with one attachment missing 'name'
        mock_source_layer.attachments.get_list.return_value = [
            {"name": "a.jpg", "id": 1},
            {"id": 2},  # Missing name
            {"name": "c.jpg", "id": 3},
        ]

        status, updated_set, copied_ids = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            CAPTURED_GLOBAL_ID,
            already_copied=None
        )

        # Only 2 valid attachments should be copied
        assert mock_target_layer.attachments.add.call_count == 2
        assert status == "2/3"  # 2 copied out of 3 total
        assert updated_set == {_expected_name(1, "a.jpg"), _expected_name(3, "c.jpg")}
        assert sorted(copied_ids) == [901, 903]

    def test_missing_id_field_skipped(self, mock_source_layer, mock_target_layer):
        """
        Attachment without 'id' should be skipped but not cause failure.
        """
        source_oid = 100
        target_oid = 200

        # Override with one attachment missing 'id'
        mock_source_layer.attachments.get_list.return_value = [
            {"name": "a.jpg", "id": 1},
            {"name": "b.jpg"},  # Missing id
            {"name": "c.jpg", "id": 3},
        ]

        status, updated_set, copied_ids = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            CAPTURED_GLOBAL_ID,
            already_copied=None
        )

        # Only 2 valid attachments should be copied
        assert mock_target_layer.attachments.add.call_count == 2
        assert status == "2/3"  # 2 copied out of 3 total
        assert updated_set == {_expected_name(1, "a.jpg"), _expected_name(3, "c.jpg")}
        assert sorted(copied_ids) == [901, 903]


class TestCopyAttachmentsAlreadyCopiedSetImmutability:
    """Test that the input already_copied set is not mutated."""

    def test_input_set_not_mutated(self, mock_source_layer, mock_target_layer):
        """
        Passing an already_copied set should not mutate the original.
        """
        source_oid = 100
        target_oid = 200

        # Create an already_copied set with one item
        original_set = {"old.jpg"}
        already_copied_copy = set(original_set)  # Save a copy to verify later

        status, updated_set, copied_ids = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            CAPTURED_GLOBAL_ID,
            already_copied=original_set
        )

        # Original set should not be mutated
        assert original_set == already_copied_copy

        # Returned set should include both the pre-seeded name and the new ones
        assert updated_set == {
            "old.jpg",
            _expected_name(1, "a.jpg"),
            _expected_name(2, "b.jpg"),
            _expected_name(3, "c.jpg"),
        }
        assert sorted(copied_ids) == [901, 902, 903]


class TestCopyAttachmentsCrossFeatureCollision:
    """Test the actual bug scenario: two captured features sharing a generic filename."""

    def test_different_captured_features_same_original_name_both_copy(
        self, mock_source_layer, mock_target_layer
    ):
        """
        Two different captured features whose single attachment both happen to
        be named "Photo1.jpg" (a common device-default filename) must each be
        uploaded under their own collision-proof name, rather than the second
        one being mistaken for an already-copied duplicate of the first.
        """
        mock_source_layer.attachments.get_list.return_value = [{"name": "Photo1.jpg", "id": 1}]

        global_id_a = "{11111111-1111-1111-1111-111111111111}"
        global_id_b = "{22222222-2222-2222-2222-222222222222}"

        status_a, set_a, ids_a = copy_attachments(
            mock_source_layer, 100, mock_target_layer, 200, global_id_a, already_copied=None
        )
        status_b, set_b, ids_b = copy_attachments(
            mock_source_layer, 101, mock_target_layer, 200, global_id_b, already_copied=None
        )

        assert status_a == "1/1"
        assert status_b == "1/1"

        # Both uploads actually happened -- the second was not skipped as
        # "already copied" just because the original filename matched.
        assert mock_target_layer.attachments.add.call_count == 2

        # The two target-side names are distinct, even though the original
        # filename and source attachment id are identical.
        assert set_a != set_b
        assert set_a == {_expected_name(1, "Photo1.jpg", global_id_a)}
        assert set_b == {_expected_name(1, "Photo1.jpg", global_id_b)}
