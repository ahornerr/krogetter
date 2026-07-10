"""Tests for JSON/JSONL storage."""

import json
import tempfile
from pathlib import Path

from krogetter.models import PriceSnapshot, TrackedItem
from krogetter.storage import Storage


def _make_item(upc: str = "0004900004825") -> TrackedItem:
    return TrackedItem(
        url=f"https://www.kingsoopers.com/p/product/{upc}",
        upc=upc,
        label="Test Product",
        location_id="62000115",
        chain="KINGSOOPERS",
        zip_code="80202",
        added_at="2026-07-09T00:00:00+00:00",
    )


def _make_snapshot(
    regular: float = 11.99,
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


class TestLoadItems:
    """Tests for load_items."""

    def test_empty_dir_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            items = storage.load_items()
            assert items == []

    def test_missing_file_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            items = storage.load_items()
            assert items == []

    def test_corrupted_json_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            # Write corrupted JSON
            storage._items_file.write_text("not valid json", encoding="utf-8")
            items = storage.load_items()
            assert items == []


class TestSaveAndLoadItems:
    """Tests for save_items and load_items round-trip."""

    def test_save_then_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            item = _make_item()
            storage.save_items([item])
            loaded = storage.load_items()
            assert len(loaded) == 1
            assert loaded[0].upc == item.upc
            assert loaded[0].label == item.label
            assert loaded[0].location_id == item.location_id
            assert loaded[0].chain == item.chain
            assert loaded[0].zip_code == item.zip_code

    def test_save_multiple_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            item1 = _make_item("0004900004825")
            item2 = _make_item("0004900004832")
            item2.label = "Second Product"
            storage.save_items([item1, item2])
            loaded = storage.load_items()
            assert len(loaded) == 2
            assert {i.upc for i in loaded} == {"0004900004825", "0004900004832"}

    def test_atomic_write_no_tmp_file_lingers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            item = _make_item()
            storage.save_items([item])
            # The .tmp file should have been renamed away
            assert not Path(storage._items_file.with_suffix(".json.tmp")).exists()
            assert storage._items_file.exists()

    def test_backup_created_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            item = _make_item()
            storage.save_items([item])
            # Save again — should create a .bak
            storage.save_items([item])
            bak_file = storage._items_file.with_suffix(".json.bak")
            assert bak_file.exists()


class TestAddItem:
    """Tests for add_item."""

    def test_add_item_present_after_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            item = _make_item()
            storage.add_item(item)
            loaded = storage.load_items()
            assert len(loaded) == 1
            assert loaded[0].upc == item.upc

    def test_add_duplicate_upc_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            item1 = _make_item("0004900004825")
            item2 = _make_item("0004900004825")
            item2.label = "Duplicate"
            storage.add_item(item1)
            try:
                storage.add_item(item2)
                assert False, "Expected ValueError"
            except ValueError as exc:
                assert "0004900004825" in str(exc)


class TestRemoveItem:
    """Tests for remove_item."""

    def test_remove_existing_item_returns_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            item = _make_item()
            storage.add_item(item)
            result = storage.remove_item(item.upc)
            assert result is True
            loaded = storage.load_items()
            assert loaded == []

    def test_remove_missing_item_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            result = storage.remove_item("0000000000000")
            assert result is False

    def test_remove_one_keeps_others(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            item1 = _make_item("0004900004825")
            item2 = _make_item("0004900004832")
            storage.add_item(item1)
            storage.add_item(item2)
            storage.remove_item(item1.upc)
            loaded = storage.load_items()
            assert len(loaded) == 1
            assert loaded[0].upc == item2.upc


class TestAppendAndLoadHistory:
    """Tests for append_history and load_history."""

    def test_append_then_load_returns_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            snap = _make_snapshot(regular=11.99, promo=8.99)
            storage.append_history("0004900004825", snap)
            history = storage.load_history("0004900004825")
            assert len(history) == 1
            assert history[0]["upc"] == "0004900004825"
            assert history[0]["regular"] == 11.99
            assert history[0]["promo"] == 8.99

    def test_most_recent_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            snap1 = _make_snapshot(regular=11.99, promo=8.99, checked_at="2026-07-09T01:00:00+00:00")
            snap2 = _make_snapshot(regular=10.99, promo=7.99, checked_at="2026-07-09T02:00:00+00:00")
            storage.append_history("0004900004825", snap1)
            storage.append_history("0004900004825", snap2)
            history = storage.load_history("0004900004825")
            assert len(history) == 2
            # Most recent first
            assert history[0]["regular"] == 10.99
            assert history[1]["regular"] == 11.99

    def test_limit_returns_only_requested_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            for i in range(5):
                snap = _make_snapshot(regular=float(i + 10))
                storage.append_history("0004900004825", snap)
            history = storage.load_history("0004900004825", limit=3)
            assert len(history) == 3

    def test_filter_by_upc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            snap_a = _make_snapshot(regular=11.99)
            snap_b = _make_snapshot(regular=9.99)
            storage.append_history("UPC-A", snap_a)
            storage.append_history("UPC-B", snap_b)
            history_a = storage.load_history("UPC-A")
            assert len(history_a) == 1
            assert history_a[0]["upc"] == "UPC-A"
            history_b = storage.load_history("UPC-B")
            assert len(history_b) == 1
            assert history_b[0]["upc"] == "UPC-B"

    def test_corrupted_lines_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            snap = _make_snapshot(regular=11.99)
            # Write a corrupted line directly, then a valid one via append_history
            with open(storage._history_file, "w", encoding="utf-8") as fh:
                fh.write("this is not json\n")
            storage.append_history("0004900004825", snap)
            history = storage.load_history("0004900004825")
            # Should only have the valid entry
            assert len(history) == 1
            assert history[0]["upc"] == "0004900004825"

    def test_empty_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            history = storage.load_history("0004900004825")
            assert history == []

    def test_blank_lines_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            # Write blank lines followed by a valid entry
            with open(storage._history_file, "w", encoding="utf-8") as fh:
                fh.write("\n")
                fh.write("  \n")
                fh.write(json.dumps({"upc": "0004900004825", "regular": 11.99, "promo": 0.0, "promo_description": None, "checked_at": "2026-07-09T00:00:00+00:00"}) + "\n")
            history = storage.load_history("0004900004825")
            assert len(history) == 1

    def test_offer_fields_persisted_in_history(self) -> None:
        """Verify that offer_template, offer_start, offer_end,
        and fulfillment_price_string are stored and retrieved."""
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            snap = _make_snapshot(
                regular=11.99,
                promo=11.99,
                promo_description="Buy 2 Get 1 Free",
                offer_template="MUST_BUY",
                offer_start="2026-07-08",
                offer_end="2026-07-21",
                fulfillment_price_string="Buy 2 Get 1 Free",
            )
            storage.append_history("0004900004825", snap)
            history = storage.load_history("0004900004825")
            assert len(history) == 1
            entry = history[0]
            assert entry["offer_template"] == "MUST_BUY"
            assert entry["offer_start"] == "2026-07-08"
            assert entry["offer_end"] == "2026-07-21"
            assert entry["fulfillment_price_string"] == "Buy 2 Get 1 Free"

    def test_offer_fields_none_by_default(self) -> None:
        """Verify that offer fields default to None in history."""
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            snap = _make_snapshot(regular=11.99)
            storage.append_history("0004900004825", snap)
            history = storage.load_history("0004900004825")
            assert len(history) == 1
            entry = history[0]
            assert entry["offer_template"] is None
            assert entry["offer_start"] is None
            assert entry["offer_end"] is None
            assert entry["fulfillment_price_string"] is None
