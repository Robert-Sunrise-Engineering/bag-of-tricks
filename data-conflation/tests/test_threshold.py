import pytest
from conflate.threshold import format_threshold_both_units


class TestFormatThresholdBothUnits:
    """Tests for format_threshold_both_units function."""

    def test_format_with_feet_source(self):
        """Test formatting with feet as source unit."""
        value = 12.0
        source_units = "feet"

        result = format_threshold_both_units(value, source_units)

        # Result should contain both feet and meters
        assert "12.00 ft" in result or "12.0 ft" in result
        assert "m" in result  # Should contain meters abbreviation
        # Verify both unit values are present by checking format
        assert "(" in result and ")" in result

    def test_format_with_meters_source(self):
        """Test formatting with meters as source unit."""
        value = 3.66
        source_units = "meters"

        result = format_threshold_both_units(value, source_units)

        # Result should contain both meters and feet
        assert "3.66 m" in result
        assert "ft" in result  # Should contain feet abbreviation
        # Verify both unit values are present
        assert "(" in result and ")" in result

    def test_format_contains_both_numeric_values(self):
        """Test that the formatted string contains both the original and converted values."""
        value = 10.0
        source_units = "feet"

        result = format_threshold_both_units(value, source_units)

        # The result format is "10.00 ft (X.XX m)"
        # Extract both numbers to verify conversion
        assert "10.00" in result  # Original value
        # The conversion of 10 feet to meters should be around 3.05
        # (10 / 3.28084 ≈ 3.048)
        assert "3.05" in result or "3.04" in result or "3.048" in result
        # Both unit abbreviations should be present
        assert "ft" in result
        assert "m" in result

    def test_format_case_insensitive(self):
        """Test that source_units is case-insensitive."""
        value = 5.0

        result_lower = format_threshold_both_units(value, "feet")
        result_upper = format_threshold_both_units(value, "FEET")
        result_mixed = format_threshold_both_units(value, "Feet")

        # All should produce the same result
        assert result_lower == result_upper == result_mixed

    def test_invalid_source_units_raises_value_error(self):
        """Test that invalid source_units raises ValueError."""
        value = 12.0
        source_units = "kilometers"

        with pytest.raises(ValueError) as exc_info:
            format_threshold_both_units(value, source_units)

        assert "Invalid source_units" in str(exc_info.value)
        assert "kilometers" in str(exc_info.value)

    def test_format_meters_conversion_accuracy(self):
        """Test that meters to feet conversion is accurate."""
        value = 1.0
        source_units = "meters"

        result = format_threshold_both_units(value, source_units)

        # 1 meter * 3.28084 = 3.28084, formatted to 2 decimals = 3.28
        assert "1.00 m (3.28 ft)" == result

    def test_format_feet_conversion_accuracy(self):
        """Test that feet to meters conversion is accurate."""
        value = 3.28084
        source_units = "feet"

        result = format_threshold_both_units(value, source_units)

        # 3.28084 feet / 3.28084 = 1.0 meter, formatted to 2 decimals = 1.00
        assert "3.28 ft (1.00 m)" == result
