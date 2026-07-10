"""Tests for the invisible_playwright-based kroger_web module."""

import json
import pathlib
from unittest.mock import MagicMock, patch

import httpx

from krogetter.api.kroger_web import (
    _parse_price,
    _parse_product_data,
    fetch_product,
    fetch_product_data,
    prepare_session,
)

FIXTURE_PATH = pathlib.Path(__file__).resolve().parent / "product_data.json"


def load_fixture() -> dict:
    """Load the product data fixture (from product v2 API response shape)."""
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def _mock_httpx_client(response_data: dict | None = None, status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Client that returns the given response data."""
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.status_code = status_code
    if response_data is not None:
        mock_response.json.return_value = response_data
        mock_response.text = json.dumps(response_data)
    else:
        mock_response.json.return_value = {}
        mock_response.text = "{}"
    mock_client.get.return_value = mock_response
    mock_client.post.return_value = mock_response
    return mock_client


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
# _parse_product_data
# ---------------------------------------------------------------------------

class TestParseProductData:
    def test_parses_product_from_fixture(self) -> None:
        product_data = load_fixture()
        product = _parse_product_data(product_data, "0004900004825")
        assert product is not None
        assert product.upc == "0004900004825"
        assert product.product_id == "0004900004825"
        assert "Coca-Cola" in product.description
        assert product.brand == "Coca-Cola"
        assert product.size == "12pk"
        assert "Beverages" in product.categories
        assert product.image_url is not None

    def test_parses_price_correctly(self) -> None:
        product_data = load_fixture()
        product = _parse_product_data(product_data, "0004900004825")
        assert product is not None
        assert product.price is not None
        assert product.price.regular == 11.99

    def test_parses_offers_correctly(self) -> None:
        product_data = load_fixture()
        product = _parse_product_data(product_data, "0004900004825")
        assert product is not None
        assert product.price is not None
        assert product.price.promo_description == "Buy 2 Get 1 Free"
        assert product.price.offer_template == "MUST_BUY"
        assert product.price.offer_start == "2026-07-08T00:00:00"
        assert product.price.offer_end == "2026-07-21T23:59:59"

    def test_parses_fulfillment_price_string(self) -> None:
        product_data = load_fixture()
        product = _parse_product_data(product_data, "0004900004825")
        assert product is not None
        assert product.price is not None
        assert product.price.fulfillment_price_string == "Buy 2 Get 1 Free"

    def test_has_offer_is_true_when_offer_exists(self) -> None:
        product_data = load_fixture()
        product = _parse_product_data(product_data, "0004900004825")
        assert product is not None
        assert product.price is not None
        assert product.price.has_offer is True
        # is_on_sale is True because there's an active offer ("Buy 2 Get 1 Free")
        assert product.price.is_on_sale is True
        # effective_unit_price should be $7.99 (pay $23.98 for 3 units)
        assert product.price.effective_unit_price == 7.99

    def test_synthetic_description_prefers_fulfillment_price_string(self) -> None:
        product_data = load_fixture()
        product = _parse_product_data(product_data, "0004900004825")
        assert product is not None
        assert product.price is not None
        assert product.price.synthetic_description == "Buy 2 Get 1 Free"

    def test_parses_availability_and_inventory(self) -> None:
        """PICKUP modality: available=True, inventory_level='HIGH'."""
        product_data = load_fixture()
        product = _parse_product_data(product_data, "0004900004825", "PICKUP")
        assert product is not None
        assert product.price is not None
        assert product.price.available is True
        assert product.price.inventory_level == "HIGH"

    def test_delivery_modality_unavailable(self) -> None:
        """DELIVERY modality: available=False, inventory_level=None."""
        product_data = load_fixture()
        product = _parse_product_data(product_data, "0004900004825", "DELIVERY")
        assert product is not None
        assert product.price is not None
        assert product.price.available is False
        assert product.price.inventory_level is None

    def test_returns_none_for_empty_data(self) -> None:
        assert _parse_product_data({}, "0004900004825") is None

    def test_returns_none_for_none(self) -> None:
        assert _parse_product_data(None, "0004900004825") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# fetch_product
# ---------------------------------------------------------------------------

class TestFetchProduct:
    def test_with_url_extracts_and_parses(self) -> None:
        """fetch_product warms up Akamai, calls product API, and parses the result."""
        fixture = load_fixture()
        url = "https://www.kingsoopers.com/p/coca-cola-vanilla-zero-sugar-fridge-pack-cans-12-fl-oz-12-pack/0004900004825"

        mock_client = _mock_httpx_client({"data": {"products": [fixture]}})

        with (
            patch("invisible_playwright.InvisiblePlaywright") as mock_ip_cls,
            patch("krogetter.api.kroger_web.prepare_session", return_value=(mock_client, None)),
        ):
            mock_browser = MagicMock()
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = mock_browser
            mock_ip_cls.return_value = mock_cm

            result = fetch_product(url)

        assert result is not None
        assert result.upc == "0004900004825"
        assert result.price is not None
        assert result.price.regular == 11.99
        assert result.price.promo_description == "Buy 2 Get 1 Free"
        assert result.price.available is True
        assert result.price.inventory_level == "HIGH"

        # InvisiblePlaywright was instantiated
        mock_ip_cls.assert_called_once_with(headless=True)
        mock_cm.__enter__.assert_called_once()
        mock_cm.__exit__.assert_called_once()
        # Client was closed
        mock_client.close.assert_called_once()

    def test_with_provided_browser_does_not_close(self) -> None:
        """When a browser is provided, it is NOT closed by fetch_product."""
        fixture = load_fixture()
        url = "https://www.kingsoopers.com/p/test-product/0004900004825"

        mock_client = _mock_httpx_client({"data": {"products": [fixture]}})
        mock_browser = MagicMock()

        with patch("krogetter.api.kroger_web.prepare_session", return_value=(mock_client, None)):
            result = fetch_product(url, browser=mock_browser)

        assert result is not None
        assert result.upc == "0004900004825"

        # The provided browser MUST NOT have been closed
        mock_browser.close.assert_not_called()

    def test_returns_none_when_api_returns_no_products(self) -> None:
        """When the product API returns no products, returns None."""
        url = "https://www.kingsoopers.com/p/missing/0004900004825"

        mock_client = _mock_httpx_client({"data": {"products": []}})

        with (
            patch("invisible_playwright.InvisiblePlaywright"),
            patch("krogetter.api.kroger_web.prepare_session", return_value=(mock_client, None)),
        ):
            result = fetch_product(url)

        assert result is None

    def test_returns_none_on_session_error(self) -> None:
        """Graceful failure when prepare_session raises an exception."""
        url = "https://www.kingsoopers.com/p/error/0004900004825"

        with (
            patch("invisible_playwright.InvisiblePlaywright"),
            patch("krogetter.api.kroger_web.prepare_session", side_effect=RuntimeError("Connection timeout")),
        ):
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


# ---------------------------------------------------------------------------
# prepare_session + fetch_product_data (batch pattern)
# ---------------------------------------------------------------------------

class TestPrepareSession:
    def test_returns_client_and_default_laf_headers(self) -> None:
        """prepare_session warms up Akamai and returns an httpx client with default LAF headers."""
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_context.cookies.return_value = []
        mock_page.evaluate.return_value = "Mozilla/5.0 test"

        with patch(
            "krogetter.api.kroger_web._get_default_store_laf",
            return_value={"x-laf-object": "[]"},
        ):
            client, laf_headers = prepare_session(
                mock_browser, "https://www.kingsoopers.com/"
            )

        assert isinstance(client, httpx.Client)
        assert laf_headers is not None  # default store LAF headers

        # Homepage loaded (no robots.txt anymore)
        mock_page.goto.assert_called_once_with(
            "https://www.kingsoopers.com/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        # Page and context were closed
        mock_page.close.assert_called_once()
        mock_context.close.assert_called_once()

        client.close()


class TestFetchProductData:
    def test_fetches_and_parses_product(self) -> None:
        """fetch_product_data calls the API and parses the result."""
        fixture = load_fixture()
        mock_client = _mock_httpx_client({"data": {"products": [fixture]}})

        result = fetch_product_data(mock_client, "0004900004825")

        assert result is not None
        assert result.upc == "0004900004825"
        assert result.price is not None
        assert result.price.regular == 11.99
        assert result.price.available is True
        assert result.price.inventory_level == "HIGH"

    def test_returns_none_on_api_failure(self) -> None:
        """Returns None when the API returns an error."""
        mock_client = _mock_httpx_client(status_code=400)

        result = fetch_product_data(mock_client, "0004900004825")
        assert result is None

    def test_returns_none_when_no_products(self) -> None:
        """Returns None when the API returns an empty products list."""
        mock_client = _mock_httpx_client({"data": {"products": []}})

        result = fetch_product_data(mock_client, "0004900004825")
        assert result is None
