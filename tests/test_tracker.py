"""Tests for the polling tracker."""

from unittest.mock import MagicMock, patch

from krogetter.models import PriceSnapshot, Product, TrackedItem
from krogetter.storage import Storage
from krogetter.tracker import Tracker


def _make_item(
    upc: str = "0004900004825",
    location_id: str | None = None,
    label: str = "Test Product",
) -> TrackedItem:
    return TrackedItem(
        url=f"https://www.kingsoopers.com/p/product/{upc}",
        upc=upc,
        label=label,
        location_id=location_id,
        chain="KINGSOOPERS",
        zip_code="80202",
        added_at="2026-07-09T00:00:00+00:00",
    )


def _make_snapshot(
    regular: float = 11.99,
    promo: float = 0.0,
    promo_description: str | None = None,
    checked_at: str = "2026-07-09T00:00:00+00:00",
) -> PriceSnapshot:
    return PriceSnapshot(
        regular=regular,
        promo=promo,
        promo_description=promo_description,
        checked_at=checked_at,
    )


def _make_product(upc: str, price: PriceSnapshot) -> Product:
    return Product(
        product_id=upc,
        upc=upc,
        description="Test Product Description",
        brand="Test Brand",
        size="12 oz",
        categories=["Test"],
        price=price,
        image_url=None,
    )


class TestCheckItemNewSale:
    """Tests for check_item when a new sale is detected."""

    def test_new_sale_with_no_prior_history(self) -> None:
        """First check on a sale item returns ChangeEvent with is_new_sale=True."""
        storage = MagicMock(spec=Storage)
        storage.load_history.return_value = []  # no prior history

        item = _make_item()
        on_sale_price = _make_snapshot(regular=11.99, promo=8.99)
        product = _make_product(item.upc, on_sale_price)

        tracker = Tracker(storage=storage)
        mock_browser = MagicMock()
        tracker._get_browser = MagicMock(return_value=mock_browser)

        with patch(
            "krogetter.tracker.fetch_product_web", return_value=product
        ) as mock_fetch:
            changes = tracker.check_item(item)

            mock_fetch.assert_called_once_with(
                item.url, browser=mock_browser, zip_code=item.zip_code, modality=item.modality, store_id=item.location_id
            )

        assert len(changes) == 1
        assert changes[0].is_new_sale is True
        assert changes[0].field == "initial"
        # Verify history was appended
        storage.append_history.assert_called_once_with(item.upc, on_sale_price)


class TestCheckItemNotFound:
    """Tests for check_item when the product is not found."""

    def test_product_not_found_returns_empty(self) -> None:
        storage = MagicMock(spec=Storage)
        storage.load_history.return_value = []

        item = _make_item()

        tracker = Tracker(storage=storage)
        tracker._get_browser = MagicMock(return_value=MagicMock())

        with patch(
            "krogetter.tracker.fetch_product_web", return_value=None
        ) as mock_fetch:
            changes = tracker.check_item(item)

            mock_fetch.assert_called_once()

        assert changes == []
        # No history should be appended for a missing product
        storage.append_history.assert_not_called()

    def test_product_with_no_price_returns_empty(self) -> None:
        storage = MagicMock(spec=Storage)
        storage.load_history.return_value = []

        item = _make_item()
        product_without_price = Product(
            product_id=item.upc,
            upc=item.upc,
            description="Test",
            brand="Test",
            size=None,
            categories=[],
            price=None,
            image_url=None,
        )

        tracker = Tracker(storage=storage)
        tracker._get_browser = MagicMock(return_value=MagicMock())

        with patch(
            "krogetter.tracker.fetch_product_web",
            return_value=product_without_price,
        ):
            changes = tracker.check_item(item)

        assert changes == []
        storage.append_history.assert_not_called()


class TestCheckItemErrors:
    """Tests for check_item error handling."""

    def test_fetch_product_raises_returns_empty(self) -> None:
        storage = MagicMock(spec=Storage)
        storage.load_history.return_value = []

        item = _make_item()

        tracker = Tracker(storage=storage)
        tracker._get_browser = MagicMock(return_value=MagicMock())

        with patch(
            "krogetter.tracker.fetch_product_web",
            side_effect=ConnectionError("network error"),
        ):
            changes = tracker.check_item(item)

        assert changes == []
        storage.append_history.assert_not_called()


class TestCheckItemWithPriorHistory:
    """Tests for check_item when there is prior history."""

    def test_no_change_returns_empty(self) -> None:
        storage = MagicMock(spec=Storage)
        snap = _make_snapshot(regular=11.99, promo=8.99)
        storage.load_history.return_value = [
            {
                "upc": "0004900004825",
                "regular": 11.99,
                "promo": 8.99,
                "promo_description": None,
                "checked_at": "2026-07-08T00:00:00+00:00",
            }
        ]

        item = _make_item()
        product = _make_product(item.upc, snap)

        tracker = Tracker(storage=storage)
        tracker._get_browser = MagicMock(return_value=MagicMock())

        with patch("krogetter.tracker.fetch_product_web", return_value=product):
            changes = tracker.check_item(item)

        assert changes == []
        storage.append_history.assert_called_once_with(item.upc, snap)

    def test_sale_ended_detected(self) -> None:
        storage = MagicMock(spec=Storage)
        storage.load_history.return_value = [
            {
                "upc": "0004900004825",
                "regular": 11.99,
                "promo": 8.99,
                "promo_description": None,
                "checked_at": "2026-07-08T00:00:00+00:00",
            }
        ]

        item = _make_item()
        not_on_sale = _make_snapshot(regular=11.99, promo=0.0)
        product = _make_product(item.upc, not_on_sale)

        tracker = Tracker(storage=storage)
        tracker._get_browser = MagicMock(return_value=MagicMock())

        with patch("krogetter.tracker.fetch_product_web", return_value=product):
            changes = tracker.check_item(item)

        assert len(changes) > 0
        assert any(c.is_sale_ended for c in changes)


class TestCheckItemWebFetcher:
    """Tests for check_item via web fetcher path."""

    def test_web_fetcher_used(self) -> None:
        """The web fetcher is the sole data source."""
        storage = MagicMock(spec=Storage)
        storage.load_history.return_value = []

        item = _make_item()
        on_sale_price = _make_snapshot(regular=11.99, promo=8.99)
        product = _make_product(item.upc, on_sale_price)

        tracker = Tracker(storage=storage)
        mock_browser = MagicMock()
        tracker._get_browser = MagicMock(return_value=mock_browser)

        with patch(
            "krogetter.tracker.fetch_product_web", return_value=product
        ) as mock_web:
            changes = tracker.check_item(item)

            mock_web.assert_called_once_with(
                item.url, browser=mock_browser, zip_code=item.zip_code, modality=item.modality, store_id=item.location_id
            )

        assert len(changes) == 1
        assert changes[0].field == "initial"

    def test_browser_reused_across_checks(self) -> None:
        """Browser should be reused across multiple check_item calls."""
        storage = MagicMock(spec=Storage)
        # Start with empty history, then after first check has history
        history_responses = [[], [
            {
                "upc": "0004900004825",
                "regular": 11.99,
                "promo": 8.99,
                "promo_description": None,
                "checked_at": "2026-07-09T00:00:00+00:00",
            }
        ]]
        storage.load_history.side_effect = history_responses

        item = _make_item()
        on_sale_price = _make_snapshot(regular=11.99, promo=8.99)
        product = _make_product(item.upc, on_sale_price)

        tracker = Tracker(storage=storage)
        mock_browser = MagicMock()
        tracker._get_browser = MagicMock(return_value=mock_browser)

        with patch("krogetter.tracker.fetch_product_web", return_value=product):
            # First check
            changes1 = tracker.check_item(item)
            assert len(changes1) == 1

            # Second check — history now has prior data, so no change
            changes2 = tracker.check_item(item)
            assert len(changes2) == 0

            # _get_browser should have been called twice but returned the same mock
            assert tracker._get_browser.call_count == 2


class TestCheckOnce:
    """Tests for the check_once method."""

    def test_no_items_returns_empty(self) -> None:
        storage = MagicMock(spec=Storage)
        storage.load_items.return_value = []

        tracker = Tracker(storage=storage)
        results = tracker.check_once()

        assert results == []

    def test_one_item_with_change(self) -> None:
        storage = MagicMock(spec=Storage)

        item = _make_item()
        storage.load_items.return_value = [item]
        storage.load_history.return_value = []

        on_sale_price = _make_snapshot(regular=11.99, promo=8.99)
        product = _make_product(item.upc, on_sale_price)

        tracker = Tracker(storage=storage)
        mock_browser = MagicMock()
        tracker._get_browser = MagicMock(return_value=mock_browser)

        with patch("krogetter.tracker.fetch_product_web", return_value=product):
            results = tracker.check_once()

        assert len(results) == 1
        assert results[0][0].upc == item.upc
        assert len(results[0][1]) == 1
        assert results[0][1][0].is_new_sale is True

    def test_multiple_items_one_failing(self) -> None:
        """When one item fails, the other succeeds normally."""
        storage = MagicMock(spec=Storage)

        item_good = _make_item("0004900004825", label="Good")
        item_bad = _make_item("0004900004832", label="Bad")
        storage.load_items.return_value = [item_good, item_bad]
        storage.load_history.return_value = []

        on_sale_price = _make_snapshot(regular=11.99, promo=8.99)
        product = _make_product(item_good.upc, on_sale_price)

        tracker = Tracker(storage=storage)
        mock_browser = MagicMock()
        tracker._get_browser = MagicMock(return_value=mock_browser)

        call_count = 0

        def side_effect(url: str, browser: object, **kwargs: object) -> Product | None:
            nonlocal call_count
            call_count += 1
            if item_bad.url in url:
                raise ConnectionError("network error")
            return product

        with patch(
            "krogetter.tracker.fetch_product_web", side_effect=side_effect
        ):
            results = tracker.check_once()

        # Only the good item should be in results
        assert len(results) == 1
        assert results[0][0].upc == item_good.upc
        assert call_count == 2  # both items were attempted

    def test_item_with_no_changes_not_in_results(self) -> None:
        storage = MagicMock(spec=Storage)

        item = _make_item()
        storage.load_items.return_value = [item]
        # Prior history matches current price -> no change
        storage.load_history.return_value = [
            {
                "upc": item.upc,
                "regular": 11.99,
                "promo": 0.0,
                "promo_description": None,
                "checked_at": "2026-07-08T00:00:00+00:00",
            }
        ]

        snap = _make_snapshot(regular=11.99, promo=0.0)
        product = _make_product(item.upc, snap)

        tracker = Tracker(storage=storage)
        mock_browser = MagicMock()
        tracker._get_browser = MagicMock(return_value=mock_browser)

        with patch("krogetter.tracker.fetch_product_web", return_value=product):
            results = tracker.check_once()

        assert results == []
