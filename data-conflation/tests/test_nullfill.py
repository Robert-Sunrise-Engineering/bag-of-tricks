import pytest
from conflate.nullfill import is_null, build_field_updates


# Tests for is_null()
class TestIsNull:
    def test_is_null_with_zero(self):
        """Zero is a real value, not null."""
        assert is_null(0) is False

    def test_is_null_with_false(self):
        """False is a real value, not null."""
        assert is_null(False) is False

    def test_is_null_with_empty_string(self):
        """Empty string is null."""
        assert is_null("") is True

    def test_is_null_with_whitespace_only_string(self):
        """Whitespace-only string is null."""
        assert is_null("   ") is True

    def test_is_null_with_none(self):
        """None is null."""
        assert is_null(None) is True

    def test_is_null_with_tab_string(self):
        """String with only tabs is null."""
        assert is_null("\t\t") is True

    def test_is_null_with_newline_string(self):
        """String with only newlines is null."""
        assert is_null("\n\n") is True

    def test_is_null_with_normal_string(self):
        """Non-empty string with content is not null."""
        assert is_null("content") is False

    def test_is_null_with_float_zero(self):
        """Float zero is a real value, not null."""
        assert is_null(0.0) is False

    def test_is_null_with_empty_list(self):
        """Empty list is a real value, not null."""
        assert is_null([]) is False

    def test_is_null_with_positive_number(self):
        """Positive numbers are not null."""
        assert is_null(1) is False

    def test_is_null_with_negative_number(self):
        """Negative numbers are not null."""
        assert is_null(-1) is False


# Tests for build_field_updates()
class TestBuildFieldUpdates:
    def test_normal_case_null_authoritative_non_null_captured(self):
        """When authoritative is null and captured is non-null, field should be in result."""
        captured = {"name": "John"}
        authoritative = {"name": None}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "name" in result
        assert result["name"] == "John"

    def test_excluded_field_not_included(self):
        """Excluded fields should never appear in result, even when eligible."""
        captured = {"email": "john@example.com"}
        authoritative = {"email": None}
        field_map = {}
        excluded = {"email"}

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "email" not in result

    def test_authoritative_value_already_non_null(self):
        """When authoritative value is non-null, field should not be updated."""
        captured = {"name": "Jane"}
        authoritative = {"name": "John"}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "name" not in result

    def test_captured_value_is_null(self):
        """When captured value is null, field should not be included."""
        captured = {"name": None}
        authoritative = {"name": None}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "name" not in result

    def test_mapped_field_normal_case(self):
        """Field mapping should work: captured_field maps to authoritative_field."""
        captured = {"alt_name": "Alice"}
        authoritative = {"name": None}
        field_map = {"alt_name": "name"}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "name" in result
        assert result["name"] == "Alice"

    def test_mapped_field_with_exclusion(self):
        """Mapped field should still be excluded if in excluded_fields."""
        captured = {"alt_name": "Alice"}
        authoritative = {"name": None}
        field_map = {"alt_name": "name"}
        excluded = {"name"}

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "name" not in result

    def test_multiple_fields_mixed(self):
        """Multiple fields with mixed conditions."""
        captured = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "phone": None,
        }
        authoritative = {
            "first_name": None,
            "last_name": "Smith",
            "email": None,
            "phone": None,
        }
        field_map = {}
        excluded = {"email"}

        result = build_field_updates(captured, authoritative, field_map, excluded)

        # first_name: auth is null, captured is non-null, not excluded -> included
        assert "first_name" in result
        assert result["first_name"] == "John"

        # last_name: auth is non-null -> not included
        assert "last_name" not in result

        # email: auth is null, captured is non-null, but excluded -> not included
        assert "email" not in result

        # phone: captured is null -> not included
        assert "phone" not in result

    def test_field_not_in_both_dicts(self):
        """Fields only in one dict should not be processed."""
        captured = {"extra_field": "value"}
        authoritative = {"name": None}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "extra_field" not in result
        assert "name" not in result

    def test_empty_dicts(self):
        """Empty dicts should return empty result."""
        result = build_field_updates({}, {}, {}, set())
        assert result == {}

    def test_captured_value_is_zero(self):
        """Zero in captured should be treated as non-null and used."""
        captured = {"count": 0}
        authoritative = {"count": None}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "count" in result
        assert result["count"] == 0

    def test_captured_value_is_false(self):
        """False in captured should be treated as non-null and used."""
        captured = {"active": False}
        authoritative = {"active": None}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "active" in result
        assert result["active"] is False

    def test_authoritative_value_is_zero(self):
        """When authoritative is 0 (non-null), should not be updated."""
        captured = {"count": 5}
        authoritative = {"count": 0}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "count" not in result

    def test_authoritative_value_is_false(self):
        """When authoritative is False (non-null), should not be updated."""
        captured = {"active": True}
        authoritative = {"active": False}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "active" not in result

    def test_authoritative_value_is_empty_string(self):
        """Empty string in authoritative is treated as null and should be filled."""
        captured = {"name": "John"}
        authoritative = {"name": ""}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "name" in result
        assert result["name"] == "John"

    def test_authoritative_value_is_whitespace_string(self):
        """Whitespace-only string in authoritative is treated as null and should be filled."""
        captured = {"name": "John"}
        authoritative = {"name": "   "}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "name" in result
        assert result["name"] == "John"

    def test_captured_value_is_empty_string(self):
        """Empty string in captured is null and should not fill."""
        captured = {"name": ""}
        authoritative = {"name": None}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "name" not in result

    def test_captured_value_is_whitespace_string(self):
        """Whitespace-only string in captured is null and should not fill."""
        captured = {"name": "   "}
        authoritative = {"name": None}
        field_map = {}
        excluded = set()

        result = build_field_updates(captured, authoritative, field_map, excluded)

        assert "name" not in result

    def test_complex_scenario_with_field_map_and_exclusions(self):
        """Complex scenario combining field mapping and exclusions."""
        captured = {
            "customer_id": "C123",
            "customer_name": "Alice",
            "phone_number": "555-1234",
        }
        authoritative = {
            "id": None,
            "name": None,
            "phone": None,
            "email": None,
        }
        field_map = {
            "customer_id": "id",
            "customer_name": "name",
            "phone_number": "phone",
        }
        excluded = {"phone"}

        result = build_field_updates(captured, authoritative, field_map, excluded)

        # id: mapped from customer_id, auth is null, captured is non-null, not excluded
        assert "id" in result
        assert result["id"] == "C123"

        # name: mapped from customer_name, auth is null, captured is non-null, not excluded
        assert "name" in result
        assert result["name"] == "Alice"

        # phone: mapped from phone_number, but excluded
        assert "phone" not in result

        # email: not in field_map and not in captured, so not processed
        assert "email" not in result
