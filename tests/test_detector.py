"""Tests for change detection logic."""

from krogetter.detector import detect_change
from krogetter.models import PriceSnapshot


def _make_snapshot(
    regular: float,
    promo: float = 0.0,
    promo_description: str | None = None,
    checked_at: str = "2026-07-09T00:00:00+00:00",
    offer_template: str | None = None,
    offer_start: str | None = None,
    offer_end: str | None = None,
    fulfillment_price_string: str | None = None,
) -> PriceSnapshot:
    return PriceSnapshot(
        regular=regular,
        promo=promo,
        promo_description=promo_description,
        checked_at=checked_at,
        offer_template=offer_template,
        offer_start=offer_start,
        offer_end=offer_end,
        fulfillment_price_string=fulfillment_price_string,
    )


class TestDetectChangeInitial:
    """Tests for detect_change with old=None (first check)."""

    def test_first_check_on_sale_returns_initial_event(self) -> None:
        snap = _make_snapshot(regular=11.99, promo=8.99)
        changes = detect_change(None, snap)
        assert len(changes) == 1
        assert changes[0].field == "initial"
        assert changes[0].is_new_sale is True
        assert changes[0].is_sale_ended is False

    def test_first_check_not_on_sale_returns_empty(self) -> None:
        snap = _make_snapshot(regular=11.99, promo=0.0)
        changes = detect_change(None, snap)
        assert changes == []

    def test_first_check_on_sale_with_description(self) -> None:
        snap = _make_snapshot(regular=11.99, promo=8.99, promo_description="Buy 2 Get 1")
        changes = detect_change(None, snap)
        assert len(changes) == 1
        assert changes[0].field == "initial"
        assert changes[0].new_value == "Buy 2 Get 1"


class TestDetectChangeNoChanges:
    """Tests for detect_change when nothing has changed."""

    def test_identical_snapshots_no_changes(self) -> None:
        snap = _make_snapshot(regular=11.99, promo=8.99)
        changes = detect_change(snap, snap)
        assert changes == []

    def test_same_values_different_objects_no_changes(self) -> None:
        old = _make_snapshot(regular=11.99, promo=8.99)
        new = _make_snapshot(regular=11.99, promo=8.99)
        changes = detect_change(old, new)
        assert changes == []

    def test_not_on_sale_no_change(self) -> None:
        old = _make_snapshot(regular=11.99)
        new = _make_snapshot(regular=11.99)
        changes = detect_change(old, new)
        assert changes == []


class TestDetectChangeRegular:
    """Tests for regular price changes."""

    def test_regular_price_dropped(self) -> None:
        old = _make_snapshot(regular=11.99)
        new = _make_snapshot(regular=9.99)
        changes = detect_change(old, new)
        assert len(changes) == 1
        assert changes[0].field == "regular"
        assert changes[0].old_value == "11.99"
        assert changes[0].new_value == "9.99"
        assert changes[0].is_new_sale is False
        assert changes[0].is_sale_ended is False

    def test_regular_price_increased(self) -> None:
        old = _make_snapshot(regular=9.99)
        new = _make_snapshot(regular=11.99)
        changes = detect_change(old, new)
        assert len(changes) == 1
        assert changes[0].field == "regular"
        assert changes[0].old_value == "9.99"
        assert changes[0].new_value == "11.99"


class TestDetectChangeNewSale:
    """Tests for sale start detection."""

    def test_new_sale_detected(self) -> None:
        old = _make_snapshot(regular=11.99)
        new = _make_snapshot(regular=11.99, promo=8.99)
        changes = detect_change(old, new)
        assert any(c.is_new_sale for c in changes)
        assert any(c.field == "promo" and c.old_value == "0.0" and c.new_value == "8.99" for c in changes)

    def test_new_sale_with_promo_description(self) -> None:
        old = _make_snapshot(regular=11.99)
        new = _make_snapshot(regular=11.99, promo=8.99, promo_description="Sale!")
        changes = detect_change(old, new)
        # Should have promo change and promo_description change
        fields = {c.field for c in changes}
        assert "promo" in fields
        assert "promo_description" in fields

    def test_new_sale_promo_equals_regular_price_sale_ended(self) -> None:
        """When promo equals regular, is_on_sale is False — sale ended."""
        old = _make_snapshot(regular=11.99, promo=8.99)
        new = _make_snapshot(regular=11.99, promo=11.99)
        changes = detect_change(old, new)
        assert any(c.is_sale_ended for c in changes)


class TestDetectChangeSaleEnded:
    """Tests for sale end detection."""

    def test_sale_ended(self) -> None:
        old = _make_snapshot(regular=11.99, promo=8.99)
        new = _make_snapshot(regular=11.99)
        changes = detect_change(old, new)
        assert any(c.is_sale_ended for c in changes)
        assert any(c.field == "promo" and c.old_value == "8.99" and c.new_value == "0.0" for c in changes)


class TestDetectChangePromoDescription:
    """Tests for promo description changes."""

    def test_promo_description_changed(self) -> None:
        old = _make_snapshot(regular=11.99, promo=8.99, promo_description="Buy 1 Get 1")
        new = _make_snapshot(regular=11.99, promo=8.99, promo_description="50% off")
        changes = detect_change(old, new)
        assert any(c.field == "promo_description" for c in changes)
        desc_change = next(c for c in changes if c.field == "promo_description")
        assert desc_change.old_value == "Buy 1 Get 1"
        assert desc_change.new_value == "50% off"

    def test_promo_description_added(self) -> None:
        old = _make_snapshot(regular=11.99, promo=8.99)
        new = _make_snapshot(regular=11.99, promo=8.99, promo_description="New sale text")
        changes = detect_change(old, new)
        assert any(c.field == "promo_description" for c in changes)

    def test_promo_description_removed(self) -> None:
        old = _make_snapshot(regular=11.99, promo=8.99, promo_description="Old sale")
        new = _make_snapshot(regular=11.99, promo=8.99)
        changes = detect_change(old, new)
        assert any(c.field == "promo_description" for c in changes)


class TestDetectChangeNoSaleNoChange:
    """Edge case: promo stays 0 but is_on_sale flips due to regular."""

    def test_promo_zero_but_regular_changed_causing_sale_end(self) -> None:
        """If regular drops below the old promo, the item goes off sale."""
        old = _make_snapshot(regular=11.99, promo=8.99)
        new = _make_snapshot(regular=7.99, promo=8.99)
        # new.promo(8.99) > new.regular(7.99) → is_on_sale is False
        changes = detect_change(old, new)
        assert any(c.is_sale_ended for c in changes)


class TestDetectChangeOffer:
    """Tests for offer-related change detection."""

    def test_first_check_with_offer_no_sale_fires_initial(self) -> None:
        """First check with has_offer but not is_on_sale should fire initial event."""
        snap = _make_snapshot(
            regular=11.99, promo=0.0, offer_template="MUST_BUY",
            fulfillment_price_string="Buy 2 Get 1 Free",
        )
        changes = detect_change(None, snap)
        assert len(changes) == 1
        assert changes[0].field == "initial"
        assert changes[0].is_new_sale is True
        assert changes[0].is_sale_ended is False

    def test_offer_template_added(self) -> None:
        """New offer_template should be detected."""
        old = _make_snapshot(regular=11.99)
        new = _make_snapshot(regular=11.99, offer_template="MUST_BUY")
        changes = detect_change(old, new)
        assert any(c.field == "offer_template" and c.old_value == "" for c in changes)

    def test_offer_template_changed(self) -> None:
        """Changed offer_template should be detected."""
        old = _make_snapshot(regular=11.99, offer_template="MUST_BUY")
        new = _make_snapshot(regular=11.99, offer_template="MUST_BUY_X")
        changes = detect_change(old, new)
        assert any(
            c.field == "offer_template" and c.old_value == "MUST_BUY" for c in changes
        )

    def test_offer_template_removed(self) -> None:
        """Removed offer_template should be detected."""
        old = _make_snapshot(regular=11.99, offer_template="MUST_BUY")
        new = _make_snapshot(regular=11.99)
        changes = detect_change(old, new)
        assert any(c.field == "offer_template" and c.new_value == "" for c in changes)

    def test_offer_start_changed(self) -> None:
        """Changed offer_start should be detected."""
        old = _make_snapshot(regular=11.99, offer_start="2026-07-01")
        new = _make_snapshot(regular=11.99, offer_start="2026-07-08")
        changes = detect_change(old, new)
        assert any(
            c.field == "offer_start" and c.old_value == "2026-07-01"
            and c.new_value == "2026-07-08"
            for c in changes
        )

    def test_offer_end_changed(self) -> None:
        """Changed offer_end should be detected."""
        old = _make_snapshot(regular=11.99, offer_end="2026-07-14")
        new = _make_snapshot(regular=11.99, offer_end="2026-07-21")
        changes = detect_change(old, new)
        assert any(
            c.field == "offer_end" and c.old_value == "2026-07-14"
            and c.new_value == "2026-07-21"
            for c in changes
        )

    def test_offer_added_is_new_sale(self) -> None:
        """Going from no offer to having an offer should set is_new_sale."""
        old = _make_snapshot(regular=11.99)
        new = _make_snapshot(
            regular=11.99, offer_template="MUST_BUY",
            offer_start="2026-07-08", offer_end="2026-07-21",
            fulfillment_price_string="Buy 2 Get 1 Free",
        )
        changes = detect_change(old, new)
        assert any(c.is_new_sale for c in changes)
        assert not any(c.is_sale_ended for c in changes)

    def test_offer_ended_is_sale_ended(self) -> None:
        """Going from having an offer to no offer should set is_sale_ended."""
        old = _make_snapshot(
            regular=11.99, offer_template="MUST_BUY",
            fulfillment_price_string="Buy 2 Get 1 Free",
        )
        new = _make_snapshot(regular=11.99)
        changes = detect_change(old, new)
        assert any(c.is_sale_ended for c in changes)
