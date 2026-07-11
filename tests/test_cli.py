"""Tests for the CLI using Click's CliRunner."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from krogetter.cli import main
from krogetter.models import PriceSnapshot, Product, TrackedItem


@pytest.fixture
def runner() -> CliRunner:
    """Create a Click CLI test runner."""
    return CliRunner()


def _make_product(upc: str = "0004900004825") -> Product:
    """Create a mock Product for testing."""
    return Product(
        product_id=upc,
        upc=upc,
        description="Coca-Cola Classic 12 Pack",
        brand="Coca-Cola",
        size="12pk",
        categories=["Beverages"],
        price=PriceSnapshot(regular=11.99, promo=0.0, promo_description=None, checked_at="2026-07-10T00:00:00+00:00"),
        image_url=None,
    )


# --------------------------------------------------------------------------- #
#  add
# --------------------------------------------------------------------------- #


def test_add_with_url(runner):
    """add with a valid URL should parse UPC, fetch product, and add to storage."""
    storage_mock = MagicMock()

    with (
        patch("krogetter.cli.Storage", return_value=storage_mock),
        patch("krogetter.cli.Tracker.fetch_product_for_item", return_value=_make_product()),
    ):
        result = runner.invoke(
            main,
            [
                "add",
                "https://www.kingsoopers.com/p/coca-cola/0004900004825",
            ],
        )

    assert result.exit_code == 0
    assert "Added:" in result.stdout
    assert "0004900004825" in result.stdout

    # Verify storage.add_item was called
    storage_mock.add_item.assert_called_once()
    added_item: TrackedItem = storage_mock.add_item.call_args[0][0]
    assert added_item.upc == "0004900004825"
    assert "Coca-Cola" in added_item.label
    assert added_item.location_id is None
    assert added_item.chain == ""
    assert added_item.zip_code == ""


def test_add_with_bare_upc(runner):
    """add with a bare 13-digit UPC should work."""
    storage_mock = MagicMock()

    with (
        patch("krogetter.cli.Storage", return_value=storage_mock),
        patch("krogetter.cli.Tracker.fetch_product_for_item", return_value=_make_product()),
    ):
        result = runner.invoke(main, ["add", "0004900004825"])

    assert result.exit_code == 0
    assert "Added:" in result.stdout
    storage_mock.add_item.assert_called_once()
    added_item = storage_mock.add_item.call_args[0][0]
    assert added_item.upc == "0004900004825"


def test_add_fetch_fails(runner):
    """add should error if the product API can't be fetched."""
    storage_mock = MagicMock()

    with (
        patch("krogetter.cli.Storage", return_value=storage_mock),
        patch("krogetter.cli.Tracker.fetch_product_for_item", return_value=None),
    ):
        result = runner.invoke(
            main,
            ["add", "https://www.kingsoopers.com/p/coca-cola/0004900004825"],
        )

    assert result.exit_code != 0
    assert "Could not fetch" in result.output
    storage_mock.add_item.assert_not_called()


def test_add_duplicate_upc(runner):
    """add with a UPC that already exists should error."""
    storage_mock = MagicMock()
    storage_mock.add_item.side_effect = ValueError(
        "UPC '0004900004825' is already being tracked"
    )

    with (
        patch("krogetter.cli.Storage", return_value=storage_mock),
        patch("krogetter.cli.Tracker.fetch_product_for_item", return_value=_make_product()),
    ):
        result = runner.invoke(main, ["add", "0004900004825"])

    assert result.exit_code == 1
    assert (
        "already being tracked" in result.stderr
        or "already being tracked" in result.stdout
    )


def test_add_invalid_url(runner):
    """add with an invalid URL should error."""
    result = runner.invoke(main, ["add", "not-a-url"])

    assert result.exit_code == 1
    assert "13-digit" in result.stderr or "13-digit" in result.stdout


# --------------------------------------------------------------------------- #
#  list
# --------------------------------------------------------------------------- #


def test_list_with_items(runner):
    """list with tracked items should show a table."""
    item = TrackedItem(
        url="https://www.kingsoopers.com/p/product/0004900004825",
        upc="0004900004825",
        label="Coke Classic",
        added_at="2026-07-09T00:00:00+00:00",
    )

    storage_mock = MagicMock()
    storage_mock.load_items.return_value = [item]
    storage_mock.load_history.return_value = [
        {
            "upc": "0004900004825",
            "regular": 11.99,
            "promo": 8.99,
            "promo_description": None,
            "checked_at": "2026-07-09T12:00:00+00:00",
            "offer_template": None,
            "offer_start": None,
            "offer_end": None,
            "fulfillment_price_string": None,
        }
    ]

    with patch("krogetter.cli.Storage", return_value=storage_mock):
        result = runner.invoke(main, ["list"])

    assert result.exit_code == 0
    assert "0004900004825" in result.stdout
    assert "Coke Classic" in result.stdout
    assert "$8.99" in result.stdout
    assert "Yes" in result.stdout


def test_list_no_items(runner):
    """list with no tracked items should show message."""
    storage_mock = MagicMock()
    storage_mock.load_items.return_value = []

    with patch("krogetter.cli.Storage", return_value=storage_mock):
        result = runner.invoke(main, ["list"])

    assert result.exit_code == 0
    assert "No tracked items" in result.stdout


def test_list_with_offer_info(runner):
    """list should show offer info when present in history."""
    item = TrackedItem(
        url="https://www.kingsoopers.com/p/product/0004900004825",
        upc="0004900004825",
        label="Coke Classic",
        added_at="2026-07-09T00:00:00+00:00",
    )

    storage_mock = MagicMock()
    storage_mock.load_items.return_value = [item]
    storage_mock.load_history.return_value = [
        {
            "upc": "0004900004825",
            "regular": 11.99,
            "promo": 11.99,
            "promo_description": "Buy 2 Get 1 Free",
            "checked_at": "2026-07-09T12:00:00+00:00",
            "offer_template": "MUST_BUY",
            "offer_start": "2026-07-08",
            "offer_end": "2026-07-21",
            "fulfillment_price_string": "Buy 2 Get 1 Free",
        }
    ]

    with patch("krogetter.cli.Storage", return_value=storage_mock):
        result = runner.invoke(main, ["list"])

    assert result.exit_code == 0
    assert "Buy 2 Get 1 Free" in result.stdout


def test_list_without_history(runner):
    """list with items that have no history should show N/A."""
    item = TrackedItem(
        url="https://www.kingsoopers.com/p/product/0004900004825",
        upc="0004900004825",
        label="Coke Classic",
        added_at="2026-07-09T00:00:00+00:00",
    )

    storage_mock = MagicMock()
    storage_mock.load_items.return_value = [item]
    storage_mock.load_history.return_value = []

    with patch("krogetter.cli.Storage", return_value=storage_mock):
        result = runner.invoke(main, ["list"])

    assert result.exit_code == 0
    assert "N/A" in result.stdout
    assert "Never" in result.stdout


# --------------------------------------------------------------------------- #
#  remove
# --------------------------------------------------------------------------- #


def test_remove_existing_item(runner):
    """remove with existing UPC should succeed."""
    storage_mock = MagicMock()
    storage_mock.remove_item.return_value = True

    with patch("krogetter.cli.Storage", return_value=storage_mock):
        result = runner.invoke(main, ["remove", "0004900004825"])

    assert result.exit_code == 0
    assert "Removed item" in result.stdout
    storage_mock.remove_item.assert_called_once_with("0004900004825")


def test_remove_nonexistent_item(runner):
    """remove with non-existent UPC should print message."""
    storage_mock = MagicMock()
    storage_mock.remove_item.return_value = False

    with patch("krogetter.cli.Storage", return_value=storage_mock):
        result = runner.invoke(main, ["remove", "0004900004825"])

    assert result.exit_code == 0
    assert "No tracked item found" in result.stdout


def test_remove_with_url(runner):
    """remove with a URL should parse and remove by UPC."""
    storage_mock = MagicMock()
    storage_mock.remove_item.return_value = True

    with patch("krogetter.cli.Storage", return_value=storage_mock):
        result = runner.invoke(
            main,
            ["remove", "https://www.kingsoopers.com/p/product/0004900004825"],
        )

    assert result.exit_code == 0
    assert "Removed item" in result.stdout
    storage_mock.remove_item.assert_called_once_with("0004900004825")


# --------------------------------------------------------------------------- #
#  check
# --------------------------------------------------------------------------- #


def test_check_with_changes(runner):
    """check (all) with changes should print them."""
    from krogetter.detector import ChangeEvent

    item = TrackedItem(
        url="https://www.kingsoopers.com/p/product/0004900004825",
        upc="0004900004825",
        label="Coke Classic",
        added_at="2026-07-09T00:00:00+00:00",
    )

    change = ChangeEvent(
        field="initial",
        old_value="no data",
        new_value="Save $3.00 (25.0% off)",
        is_new_sale=True,
        is_sale_ended=False,
    )

    tracker_mock = MagicMock()
    tracker_mock.check_once.return_value = [(item, [change])]

    with (
        patch("krogetter.cli.Tracker", return_value=tracker_mock),
        patch("krogetter.cli.Storage"),
    ):
        result = runner.invoke(main, ["check"])

    assert result.exit_code == 0
    assert "Changes detected" in result.stdout
    assert "Coke Classic" in result.stdout
    assert "initial" in result.stdout
    assert "New sale" in result.stdout


def test_check_no_changes(runner):
    """check (all) with no changes should print message."""
    tracker_mock = MagicMock()
    tracker_mock.check_once.return_value = []

    with (
        patch("krogetter.cli.Tracker", return_value=tracker_mock),
        patch("krogetter.cli.Storage"),
    ):
        result = runner.invoke(main, ["check"])

    assert result.exit_code == 0
    assert "No changes detected" in result.stdout


def test_check_single_item_not_found(runner):
    """check with a UPC that isn't tracked should error."""
    storage_mock = MagicMock()
    storage_mock.load_items.return_value = []

    with patch("krogetter.cli.Storage", return_value=storage_mock):
        result = runner.invoke(main, ["check", "0004900004825"])

    assert result.exit_code == 1
    assert "No tracked item found" in result.stderr


def test_check_single_item_sale_ended(runner):
    """check a single item with sale ended."""
    from krogetter.detector import ChangeEvent

    item = TrackedItem(
        url="https://www.kingsoopers.com/p/product/0004900004825",
        upc="0004900004825",
        label="Coke Classic",
        added_at="2026-07-09T00:00:00+00:00",
    )

    change = ChangeEvent(
        field="promo",
        old_value="8.99",
        new_value="0.0",
        is_new_sale=False,
        is_sale_ended=True,
    )

    storage_mock = MagicMock()
    storage_mock.load_items.return_value = [item]

    tracker_mock = MagicMock()
    tracker_mock.check_item.return_value = [change]

    with (
        patch("krogetter.cli.Storage", return_value=storage_mock),
        patch("krogetter.cli.Tracker", return_value=tracker_mock),
    ):
        result = runner.invoke(main, ["check", "0004900004825"])

    assert result.exit_code == 0
    assert "Changes for" in result.stdout
    assert "Sale ended" in result.stdout


# --------------------------------------------------------------------------- #
#  config
# --------------------------------------------------------------------------- #


def test_config_shows_values(runner):
    """config should print configuration values."""
    result = runner.invoke(main, ["config"])

    assert result.exit_code == 0
    assert "Data Dir:" in result.stdout
    assert "Log Level:" in result.stdout
    assert "KINGSOOPERS" in result.stdout
    assert "Web Fetcher:" in result.stdout


# --------------------------------------------------------------------------- #
#  main / error cases
# --------------------------------------------------------------------------- #


def test_main_help(runner):
    """main --help should show usage."""
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Krogetter" in result.stdout
    assert "add" in result.stdout
    assert "check" in result.stdout
    assert "list" in result.stdout
    assert "remove" in result.stdout
    assert "run" in result.stdout
    assert "config" in result.stdout


def test_main_list_no_items(runner):
    """list with no items should work."""
    storage_mock = MagicMock()
    storage_mock.load_items.return_value = []

    with patch("krogetter.cli.Storage", return_value=storage_mock):
        result = runner.invoke(main, ["list"])

    assert result.exit_code == 0
    assert "No tracked items" in result.stdout
