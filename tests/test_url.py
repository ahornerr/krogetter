"""Tests for the URL parsing and UPC extraction module."""

import pytest
from krogetter.url import extract_upc_or_passthrough, parse_product_url, validate_upc

# The actual product ID from the user's example URL.
# Note: this does NOT pass UPC-A check-digit validation (computed check = 3,
# actual = 5), which is common for Kroger URLs. The bot accepts it anyway.
KROGER_EXAMPLE_ID = "0004900004825"

# A 13-digit ID with a valid UPC-A check digit (for testing validate_upc).
# Data digits: 004900000482 → check digit 3 → "0004900004823"
VALID_UPC = "0004900004823"


class TestParseProductUrl:
    EXAMPLE_URL = (
        "https://www.kingsoopers.com/p/"
        "coca-cola-vanilla-zero-sugar-fridge-pack-cans-12-fl-oz-12-pack/"
        f"{KROGER_EXAMPLE_ID}"
    )

    def test_extracts_id_from_real_example_url(self) -> None:
        """The user's actual example URL must work — this is the critical test."""
        upc = parse_product_url(self.EXAMPLE_URL)
        assert upc == KROGER_EXAMPLE_ID

    def test_supports_fredmeyer_domain(self) -> None:
        url = f"https://www.fredmeyer.com/p/some-product/{KROGER_EXAMPLE_ID}"
        assert parse_product_url(url) == KROGER_EXAMPLE_ID

    def test_supports_kroger_domain(self) -> None:
        url = f"https://www.kroger.com/p/some-product/{KROGER_EXAMPLE_ID}"
        assert parse_product_url(url) == KROGER_EXAMPLE_ID

    def test_supports_smithsfoodanddrug_domain(self) -> None:
        url = f"https://www.smithsfoodanddrug.com/p/some-product/{KROGER_EXAMPLE_ID}"
        assert parse_product_url(url) == KROGER_EXAMPLE_ID

    def test_supports_all_brand_domains(self) -> None:
        """Every Kroger family domain should be accepted."""
        for domain in [
            "kingsoopers.com", "kroger.com", "fredmeyer.com", "ralphs.com",
            "smithsfoodanddrug.com", "harristeeter.com", "frysfood.com",
            "qfc.com", "dillons.com", "bakersplus.com", "citymarket.com",
            "food4less.com", "foodsco.net", "gerbes.com", "jaycfoods.com",
            "marianos.com", "metromarket.net", "pay-less.com", "picknsave.com",
        ]:
            url = f"https://www.{domain}/p/product/{KROGER_EXAMPLE_ID}"
            assert parse_product_url(url) == KROGER_EXAMPLE_ID, f"Failed for {domain}"

    def test_raises_valueerror_for_unknown_domain(self) -> None:
        with pytest.raises(ValueError, match="Not a recognized Kroger family domain"):
            parse_product_url(f"https://www.amazon.com/p/some-product/{KROGER_EXAMPLE_ID}")

    def test_raises_valueerror_when_no_id_in_path(self) -> None:
        with pytest.raises(ValueError, match="No 13-digit product ID found"):
            parse_product_url("https://www.kingsoopers.com/p/some-product")

    def test_accepts_id_without_valid_check_digit(self) -> None:
        """Kroger IDs don't always pass UPC-A — must not reject them."""
        url = f"https://www.kingsoopers.com/p/product/{KROGER_EXAMPLE_ID}"
        assert parse_product_url(url) == KROGER_EXAMPLE_ID

    def test_strips_trailing_slash(self) -> None:
        url = f"https://www.kingsoopers.com/p/product/{KROGER_EXAMPLE_ID}/"
        assert parse_product_url(url) == KROGER_EXAMPLE_ID


class TestValidateUpc:
    def test_valid_upc_passes(self) -> None:
        assert validate_upc(VALID_UPC) is True

    def test_kroger_example_id_fails_check_digit(self) -> None:
        """The user's real URL ID doesn't pass — documents why we don't enforce."""
        assert validate_upc(KROGER_EXAMPLE_ID) is False

    def test_non_numeric_fails(self) -> None:
        assert validate_upc("00049000048abc") is False

    def test_wrong_length_fails(self) -> None:
        assert validate_upc("00049000048") is False

    def test_strips_whitespace(self) -> None:
        assert validate_upc(f" {VALID_UPC} ") is True


class TestExtractUpcOrPassthrough:
    URL = f"https://www.kingsoopers.com/p/some-product/{KROGER_EXAMPLE_ID}"

    def test_parses_url(self) -> None:
        assert extract_upc_or_passthrough(self.URL) == KROGER_EXAMPLE_ID

    def test_passthrough_bare_id(self) -> None:
        assert extract_upc_or_passthrough(KROGER_EXAMPLE_ID) == KROGER_EXAMPLE_ID

    def test_passthrough_valid_upc(self) -> None:
        assert extract_upc_or_passthrough(VALID_UPC) == VALID_UPC

    def test_rejects_short_id(self) -> None:
        with pytest.raises(ValueError, match="Expected a 13-digit product ID"):
            extract_upc_or_passthrough("12345")

    def test_rejects_non_numeric_id(self) -> None:
        with pytest.raises(ValueError, match="Expected a 13-digit product ID"):
            extract_upc_or_passthrough("00049000048ab")
