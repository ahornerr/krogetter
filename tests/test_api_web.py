"""Tests for the Camoufox-based kroger_web module."""

import json
import pathlib
from unittest.mock import MagicMock, Mock, patch

from krogetter.api.kroger_web import (
    _extract_initial_state,
    _get_product_data,
    _parse_price,
    _parse_product_from_state,
    fetch_product,
)

FIXTURE_PATH = pathlib.Path(__file__).resolve().parent / "initial_state.json"


def load_fixture() -> dict:
    """Load the __INITIAL_STATE__ JSON fixture."""
    with open(FIXTURE_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------

class TestParsePrice:
    def test_usd_format(self) -> None:
        assert _parse_price("USD 11.99") == 11.99

    def test_dollar_sign_format(self) -> None:
        assert _parse_price("$11.99") == 11.99

    def test_plain_number(self) -> None:
        assert _parse_price("11.99") == 11.99

    def test_whole_dollar(self) -> None:
        assert _parse_price("USD 5") == 5.0

    def test_zero(self) -> None:
        assert _parse_price("USD 0") == 0.0

    def test_dollar_with_cents(self) -> None:
        assert _parse_price("$0.99") == 0.99


# ---------------------------------------------------------------------------
# _extract_initial_state
# ---------------------------------------------------------------------------

class TestExtractInitialState:
    def test_returns_dict_on_success(self) -> None:
        """_extract_initial_state returns the parsed JSON dict."""
        fixture = load_fixture()
        page = Mock()
        page.evaluate.return_value = json.dumps(fixture)
        result = _extract_initial_state(page)
        assert result == fixture

    def test_returns_none_on_null_result(self) -> None:
        """_extract_initial_state returns None when evaluate returns None."""
        page = Mock()
        page.evaluate.return_value = None
        assert _extract_initial_state(page) is None


# ---------------------------------------------------------------------------
# _get_product_data
# ---------------------------------------------------------------------------

class TestGetProductData:
    def test_primary_path_from_fixture(self) -> None:
        """Extract product data from calypso.domains.products.{upc}.data."""
        state = load_fixture()
        data = _get_product_data(state, "0004900004825")
        assert data is not None
        assert "item" in data
        assert "price" in data
        assert "offers" in data

    def test_returns_none_for_missing_upc(self) -> None:
        """Neither the domains.products path nor the useCases path has the UPC."""
        # Build a state that has no matching product in either path
        state: dict = {"calypso": {"domains": {"products": {}}}}
        assert _get_product_data(state, "9999999999999") is None

    def test_fallback_to_use_cases(self) -> None:
        """When domains.products is empty, falls back to useCases path."""
        state = load_fixture()
        # Create a state where the primary path is empty but useCases is populated
        modified = dict(state)
        modified["calypso"] = dict(state["calypso"])
        # Remove the products from domains but keep useCases
        if "products" in modified["calypso"].get("domains", {}):
            modified["calypso"]["domains"] = dict(modified["calypso"]["domains"])
            modified["calypso"]["domains"]["products"] = {}

        data = _get_product_data(modified, "0004900004825")
        assert data is not None
        assert "item" in data

    def test_returns_none_when_no_data_found(self) -> None:
        assert _get_product_data({}, "0004900004825") is None


# ---------------------------------------------------------------------------
# _parse_product_from_state
# ---------------------------------------------------------------------------

class TestParseProductFromState:
    def test_parses_product_from_fixture(self) -> None:
        state = load_fixture()
        product = _parse_product_from_state(state, "0004900004825")
        assert product is not None
        assert product.upc == "0004900004825"
        assert product.product_id == "0004900004825"
        assert "Coca-Cola" in product.description
        assert product.brand == "Coca-Cola"
        assert product.size == "12pk"
        assert "Beverages" in product.categories
        assert product.image_url is not None

    def test_parses_price_correctly(self) -> None:
        state = load_fixture()
        product = _parse_product_from_state(state, "0004900004825")
        assert product is not None
        assert product.price is not None
        assert product.price.regular == 11.99

    def test_parses_offers_correctly(self) -> None:
        state = load_fixture()
        product = _parse_product_from_state(state, "0004900004825")
        assert product is not None
        assert product.price is not None
        assert product.price.promo_description == "Buy 2 Get 1 Free"
        assert product.price.offer_template == "MUST_BUY"
        assert product.price.offer_start == "2026-07-08T00:00:00"
        assert product.price.offer_end == "2026-07-21T23:59:59"

    def test_parses_fulfillment_price_string(self) -> None:
        state = load_fixture()
        product = _parse_product_from_state(state, "0004900004825")
        assert product is not None
        assert product.price is not None
        assert product.price.fulfillment_price_string == "Buy 2 Get 1 Free"

    def test_has_offer_is_true_when_offer_exists(self) -> None:
        state = load_fixture()
        product = _parse_product_from_state(state, "0004900004825")
        assert product is not None
        assert product.price is not None
        assert product.price.has_offer is True
        # is_on_sale is True because there's an active offer ("Buy 2 Get 1 Free")
        assert product.price.is_on_sale is True
        # effective_unit_price should be $7.99 (pay $23.98 for 3 units)
        assert product.price.effective_unit_price == 7.99

    def test_synthetic_description_prefers_fulfillment_price_string(self) -> None:
        state = load_fixture()
        product = _parse_product_from_state(state, "0004900004825")
        assert product is not None
        assert product.price is not None
        assert product.price.synthetic_description == "Buy 2 Get 1 Free"

    def test_returns_none_for_empty_state(self) -> None:
        assert _parse_product_from_state({}, "0004900004825") is None

    def test_returns_none_for_state_without_product_data(self) -> None:
        assert _parse_product_from_state({"calypso": {}}, "0004900004825") is None


# ---------------------------------------------------------------------------
# fetch_product
# ---------------------------------------------------------------------------

class TestFetchProduct:
    def test_with_url_extracts_and_parses(self) -> None:
        """fetch_product navigates, extracts, and parses a product."""
        fixture = load_fixture()
        url = "https://www.kingsoopers.com/p/coca-cola-vanilla-zero-sugar-fridge-pack-cans-12-fl-oz-12-pack/0004900004825"

        with patch("camoufox.sync_api.Camoufox") as mock_camoufox_cls:
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            # Camoufox() returns a context manager; __enter__ returns the browser
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = mock_browser
            mock_camoufox_cls.return_value = mock_cm

            mock_page.evaluate.return_value = json.dumps(fixture)

            result = fetch_product(url)

        assert result is not None
        assert result.upc == "0004900004825"
        assert result.price is not None
        assert result.price.regular == 11.99
        assert result.price.promo_description == "Buy 2 Get 1 Free"

        # Verify navigation call
        mock_page.goto.assert_called_once_with(
            url, wait_until="domcontentloaded", timeout=30000
        )
        # Camoufox was instantiated
        mock_camoufox_cls.assert_called_once_with(headless=True)
        # Context manager was entered and exited
        mock_cm.__enter__.assert_called_once()
        mock_cm.__exit__.assert_called_once()

    def test_with_provided_browser_does_not_close(self) -> None:
        """When a browser is provided, it is NOT closed by fetch_product."""
        fixture = load_fixture()
        url = "https://www.kingsoopers.com/p/test-product/0004900004825"

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.evaluate.return_value = json.dumps(fixture)

        result = fetch_product(url, browser=mock_browser)

        assert result is not None
        assert result.upc == "0004900004825"

        # The provided browser MUST NOT have been closed
        mock_browser.close.assert_not_called()

    def test_returns_none_when_no_initial_state(self) -> None:
        """When the page has no __INITIAL_STATE__, returns None."""
        url = "https://www.kingsoopers.com/p/missing/0004900004825"

        with patch("camoufox.sync_api.Camoufox") as mock_camoufox_cls:
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page
            mock_camoufox_cls.return_value = mock_browser

            mock_page.evaluate.return_value = None

            result = fetch_product(url)

        assert result is None

    def test_returns_none_on_page_error(self) -> None:
        """Graceful failure when page navigation raises an exception."""
        url = "https://www.kingsoopers.com/p/error/0004900004825"

        with patch("camoufox.sync_api.Camoufox") as mock_camoufox_cls:
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page
            mock_camoufox_cls.return_value = mock_browser

            mock_page.goto.side_effect = RuntimeError("Connection timeout")

            result = fetch_product(url)

        assert result is None

    def test_bare_upc_returns_none_with_warning(self) -> None:
        """Bare UPCs are not yet supported — returns None."""
        result = fetch_product("0004900004825")
        assert result is None

    def test_invalid_bare_upc_returns_none(self) -> None:
        """Invalid bare UPC returns None."""
        result = fetch_product("abc123")
        assert result is None

    def test_invalid_url_returns_none(self) -> None:
        """A URL from a non-Kroger domain returns None."""
        result = fetch_product("https://example.com/product/0004900004825")
        assert result is None
