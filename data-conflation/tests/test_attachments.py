"""
Unit tests for conflate.attachments.copy_attachments function.

Tests the critical dedup-retry behavior and error handling without requiring
real AGOL connections. Uses mocks for all layer operations.
"""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from conflate.attachments import copy_attachments


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
        return str(file_path)

    layer.attachments.get_list.return_value = [
        {"name": "a.jpg", "id": 1},
        {"name": "b.jpg", "id": 2},
        {"name": "c.jpg", "id": 3},
    ]
    layer.attachments.download.side_effect = download_side_effect

    return layer


@pytest.fixture
def mock_target_layer():
    """Create a mock target layer."""
    layer = MagicMock()
    layer.attachments.add = MagicMock()
    return layer


class TestCopyAttachmentsBasic:
    """Basic test: first call with all new attachments."""

    def test_first_call_copies_all_attachments(self, mock_source_layer, mock_target_layer):
        """
        First call should copy all 3 attachments.

        Verifies:
        - target_layer.attachments.add is called exactly 3 times
        - status string is "3/3"
        - returned set contains all three attachment names
        """
        source_oid = 100
        target_oid = 200

        status, updated_set = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            already_copied=None
        )

        # Verify add was called exactly 3 times
        assert mock_target_layer.attachments.add.call_count == 3

        # Verify status string
        assert status == "3/3"

        # Verify the updated set contains all attachment names
        assert updated_set == {"a.jpg", "b.jpg", "c.jpg"}


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
        status1, already_copied_set = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            already_copied=None
        )

        # Verify first call worked
        assert status1 == "3/3"
        assert already_copied_set == {"a.jpg", "b.jpg", "c.jpg"}
        assert mock_target_layer.attachments.add.call_count == 3

        # Reset the mock to clear the first call's count
        mock_target_layer.attachments.add.reset_mock()

        # Second call with the already_copied set
        status2, updated_set = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            already_copied=already_copied_set
        )

        # CRITICAL: Verify add is NOT called again in the second call
        assert mock_target_layer.attachments.add.call_count == 0, \
            "Second call with already_copied set should not re-upload any attachments"

        # Verify status still reflects all attachments are copied
        assert status2 == "3/3"

        # Verify the updated set is unchanged
        assert updated_set == {"a.jpg", "b.jpg", "c.jpg"}


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
        status, updated_set = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            already_copied=None
        )

        # Verify it doesn't raise and continues processing
        # add should be called only for successful downloads (2 times: for IDs 1 and 3)
        assert mock_target_layer.attachments.add.call_count == 2

        # Status should be "2/3" (2 successfully copied out of 3 total)
        assert status == "2/3"

        # Only successfully copied attachments should be in the set
        assert updated_set == {"a.jpg", "c.jpg"}


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

        status, updated_set = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            already_copied=None
        )

        # No attachments means "0/0"
        assert status == "0/0"

        # Updated set should be empty
        assert updated_set == set()

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

        status, updated_set = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            already_copied=already_copied
        )

        # Function should not raise; should return "0/0"
        assert status == "0/0"

        # already_copied should be preserved
        assert updated_set == {"old.jpg"}


class TestCopyAttachmentsUploadFailure:
    """Test behavior when uploading fails."""

    def test_upload_failure_continues_processing(self, mock_source_layer, mock_target_layer):
        """
        If upload fails for one attachment, others should still be processed.

        Verifies:
        - Function does not raise
        - Returns status like "2/3" (2 successful uploads out of 3 total)
        - Successfully uploaded attachments are in the returned set
        - Failed attachment is NOT in the returned set
        """
        source_oid = 100
        target_oid = 200

        # Make add fail for the second call
        call_count = [0]

        def add_with_failure(oid, file_path):
            call_count[0] += 1
            if call_count[0] == 2:  # Fail on second call
                raise Exception("Simulated upload failure")

        mock_target_layer.attachments.add.side_effect = add_with_failure

        status, updated_set = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            already_copied=None
        )

        # Function should not raise
        # add should be called 3 times (one fails but others succeed)
        assert mock_target_layer.attachments.add.call_count == 3

        # Status should reflect only successful uploads: "2/3"
        assert status == "2/3"

        # Only successfully uploaded attachments should be in the set
        # (assuming a.jpg succeeds, b.jpg fails, c.jpg succeeds)
        assert updated_set == {"a.jpg", "c.jpg"}


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

        status, updated_set = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            already_copied=None
        )

        # Only 2 valid attachments should be copied
        assert mock_target_layer.attachments.add.call_count == 2
        assert status == "2/3"  # 2 copied out of 3 total
        assert updated_set == {"a.jpg", "c.jpg"}

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

        status, updated_set = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            already_copied=None
        )

        # Only 2 valid attachments should be copied
        assert mock_target_layer.attachments.add.call_count == 2
        assert status == "2/3"  # 2 copied out of 3 total
        assert updated_set == {"a.jpg", "c.jpg"}


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

        status, updated_set = copy_attachments(
            mock_source_layer,
            source_oid,
            mock_target_layer,
            target_oid,
            already_copied=original_set
        )

        # Original set should not be mutated
        assert original_set == already_copied_copy

        # Returned set should include both old and new attachments
        assert updated_set == {"old.jpg", "a.jpg", "b.jpg", "c.jpg"}
