"""Tests for domain model dataclasses."""

from krogetter.models import PriceSnapshot, Product, TrackedItem


class TestPriceSnapshot:
    def test_is_on_sale_true_when_promo_less_than_regular(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=8.99, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.is_on_sale is True

    def test_is_on_sale_false_when_promo_zero(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=0.0, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.is_on_sale is False

    def test_is_on_sale_false_when_promo_equals_regular(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=11.99, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.is_on_sale is False

    def test_is_on_sale_false_when_promo_greater_than_regular(self) -> None:
        snap = PriceSnapshot(regular=8.99, promo=11.99, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.is_on_sale is False

    def test_current_price_returns_promo_when_on_sale(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=8.99, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.current_price == 8.99

    def test_current_price_returns_regular_when_not_on_sale(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=0.0, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.current_price == 11.99

    def test_savings_on_sale(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=8.99, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.savings == 3.0

    def test_savings_none_when_not_on_sale(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=0.0, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.savings is None

    def test_savings_percent_on_sale(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=8.99, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.savings_percent == 25.0  # 3.0/11.99 ≈ 0.2502 → 25.0

    def test_savings_percent_none_when_not_on_sale(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=0.0, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.savings_percent is None

    def test_synthetic_description_returns_promo_description(self) -> None:
        snap = PriceSnapshot(
            regular=11.99, promo=8.99, promo_description="Buy 2 Get 1 Free",
            checked_at="2024-01-01T00:00:00+00:00",
        )
        assert snap.synthetic_description == "Buy 2 Get 1 Free"

    def test_synthetic_description_computed_when_no_promo_description(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=8.99, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.synthetic_description == "Save $3.00 (25.0% off)"

    def test_synthetic_description_none_when_not_on_sale(self) -> None:
        snap = PriceSnapshot(regular=11.99, promo=0.0, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.synthetic_description is None

    def test_savings_percent_none_when_regular_is_zero(self) -> None:
        """Guards division-by-zero path."""
        snap = PriceSnapshot(regular=0.0, promo=0.0, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.savings_percent is None

    def test_savings_rounds_to_two_decimals(self) -> None:
        """Floating-point precision: 10.00 - 3.33 = 6.67, not 6.669999..."""
        snap = PriceSnapshot(regular=10.00, promo=3.33, promo_description=None, checked_at="2024-01-01T00:00:00+00:00")
        assert snap.savings == 6.67


class TestProduct:
    def test_product_construction(self) -> None:
        price = PriceSnapshot(
            regular=11.99, promo=8.99, promo_description=None,
            checked_at="2024-01-01T00:00:00+00:00",
        )
        product = Product(
            product_id="0004900004825",
            upc="0004900004825",
            description="Coca-Cola Vanilla Zero Sugar Fridge Pack",
            brand="Coca-Cola",
            size="12 fl oz",
            categories=["Beverages", "Soda"],
            price=price,
            image_url="https://example.com/image.jpg",
        )
        assert product.product_id == "0004900004825"
        assert product.upc == "0004900004825"
        assert product.description == "Coca-Cola Vanilla Zero Sugar Fridge Pack"
        assert product.brand == "Coca-Cola"
        assert product.size == "12 fl oz"
        assert product.categories == ["Beverages", "Soda"]
        assert product.price is price
        assert product.image_url == "https://example.com/image.jpg"

    def test_product_without_price(self) -> None:
        product = Product(
            product_id="0004900004825",
            upc="0004900004825",
            description="Some Product",
            brand="Some Brand",
            size=None,
            categories=[],
            price=None,
            image_url=None,
        )
        assert product.price is None


class TestTrackedItem:
    def test_tracked_item_construction(self) -> None:
        item = TrackedItem(
            url="https://www.kingsoopers.com/p/some-product/0004900004825",
            upc="0004900004825",
            label="Coca-Cola 12-pack",
            location_id="62000115",
            chain="KINGSOOPERS",
            zip_code="80202",
            added_at="2024-01-01T00:00:00+00:00",
        )
        assert item.url == "https://www.kingsoopers.com/p/some-product/0004900004825"
        assert item.upc == "0004900004825"
        assert item.label == "Coca-Cola 12-pack"
        assert item.location_id == "62000115"
        assert item.chain == "KINGSOOPERS"
        assert item.zip_code == "80202"
        assert item.added_at == "2024-01-01T00:00:00+00:00"
