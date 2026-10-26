"""Regression tests for tracker summaries, session refresh, and shutdown."""

import logging
import threading
from unittest.mock import MagicMock, patch

import httpx
import pytest

from krogetter.models import PriceSnapshot, Product, TrackedItem
from krogetter.storage import Storage
from krogetter.tracker import CheckSummary, Tracker, _store_key


def _item(upc: str) -> TrackedItem:
    return TrackedItem(
        url=f"https://www.kingsoopers.com/p/{upc}",
        upc=upc,
        label=upc,
        chain="KINGSOOPERS",
        zip_code="80202",
        added_at="2026-07-09T00:00:00+00:00",
    )


def _product(upc: str) -> Product:
    return Product(
        product_id=upc,
        upc=upc,
        description="x",
        brand=None,
        size=None,
        categories=[],
        price=PriceSnapshot(
            regular=1, promo=0, promo_description=None, checked_at="2026-07-09T00:00:00+00:00"
        ),
        image_url=None,
    )


def test_batch_refresh_uses_new_open_client_for_next_item() -> None:
    storage = MagicMock(spec=Storage)
    items = [_item("one"), _item("two")]
    storage.load_items.return_value = items
    storage.load_history.return_value = []
    tracker = Tracker(storage)
    first, refreshed = httpx.Client(), httpx.Client()
    calls = []
    with (
        patch(
            "krogetter.tracker.prepare_session", side_effect=[(first, None), (refreshed, None)]
        ) as prepare,
        patch(
            "krogetter.tracker.fetch_product_data",
            side_effect=lambda client, upc, *a: (
                calls.append((client, upc)),
                None if upc == "one" and client is first else _product(upc),
            )[1],
        ),
    ):
        tracker._get_browser = MagicMock(return_value=MagicMock())
        try:
            tracker.check_once()
            assert calls == [(first, "one"), (refreshed, "one"), (refreshed, "two")]
            assert prepare.call_count == 2
            assert first.is_closed
            assert not refreshed.is_closed
            assert storage.append_history.call_count == 2
            assert tracker.last_check_summary == CheckSummary(2, 2, ())
        finally:
            tracker.close()
            first.close()
            refreshed.close()


def test_setup_failure_marks_each_group_item_failed() -> None:
    storage = MagicMock(spec=Storage)
    items = [_item("a"), _item("b")]
    storage.load_items.return_value = items
    tracker = Tracker(storage)
    tracker._get_browser = MagicMock(return_value=MagicMock())
    try:
        with patch(
            "krogetter.tracker.prepare_session", side_effect=RuntimeError("setup")
        ) as prepare:
            assert tracker.check_once() == []
        assert prepare.call_count == 1
        assert tracker.last_check_summary == CheckSummary(2, 0, ("a", "b"))
    finally:
        tracker.close()


def test_stop_wakes_idle_run_promptly_and_closes_once() -> None:
    tracker = Tracker(MagicMock(spec=Storage))
    waiting = threading.Event()
    original_wait = tracker._stop_event.wait

    def wait(interval):
        waiting.set()
        return original_wait(interval)

    tracker._stop_event.wait = wait
    finished = threading.Event()
    thread = threading.Thread(target=lambda: (tracker.run(3600), finished.set()))
    thread.start()
    try:
        assert waiting.wait(2)
    finally:
        tracker.stop()
    assert finished.wait(2)
    thread.join(2)
    assert not thread.is_alive()
    tracker.close()
    assert tracker._closed


def test_close_waits_for_active_operation_and_rejects_later_calls() -> None:
    tracker = Tracker(MagicMock(spec=Storage))
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    item = _item("a")
    tracker.operation_lock = _ObservedLock(tracker.operation_lock)
    tracker._storage.load_items.return_value = [item]
    client = MagicMock()
    client.close.side_effect = closed.set
    tracker._get_session = MagicMock(return_value=(client, None))
    tracker._sessions[_store_key(item)] = (client, None)

    def fetch(*args):
        entered.set()
        assert release.wait(2)
        return _product("a")

    errors = []
    with patch("krogetter.tracker.fetch_product_data", side_effect=fetch):
        worker = threading.Thread(target=lambda: _capture(errors, tracker.check_once))
        worker.start()
        assert entered.wait(2)
        closer = threading.Thread(target=lambda: _capture(errors, tracker.close))
        closer.start()
        try:
            assert tracker.operation_lock.contender.wait(2)
            assert not closed.is_set()
        finally:
            release.set()
        worker.join(2)
        closer.join(2)
    assert not worker.is_alive() and not closer.is_alive()
    assert errors == []
    assert closed.is_set()
    client.close.assert_called_once()
    with pytest.raises(RuntimeError, match="closed"):
        tracker.check_once()


def _capture(errors, operation):
    try:
        operation()
    except Exception as exc:
        errors.append(exc)


def test_summaries_distinguish_success_and_failure() -> None:
    storage = MagicMock(spec=Storage)
    items = [_item("ok"), _item("none"), _item("bad")]
    storage.load_items.return_value = items
    storage.load_history.return_value = [
        {"regular": 1, "promo": 0, "checked_at": "2026-07-09T00:00:00+00:00"}
    ]
    tracker = Tracker(storage)
    client = httpx.Client()
    tracker._get_session = MagicMock(return_value=(client, None))

    def fetch(_client, upc, *_args):
        if upc == "none":
            return None
        if upc == "bad":
            raise ValueError("fetch failure")
        return _product(upc)

    try:
        with patch("krogetter.tracker.fetch_product_data", side_effect=fetch):
            assert tracker.check_once() == []  # unchanged item is successful
        assert tracker.last_check_summary == CheckSummary(3, 1, ("none", "bad"))
        assert tracker.last_check_summary.attempted == (
            tracker.last_check_summary.succeeded + tracker.last_check_summary.failed
        )
        storage.load_items.return_value = []
        tracker.check_once()
        assert tracker.last_check_summary == CheckSummary()
    finally:
        tracker.close()
        client.close()


def test_run_failure_logs_warning_not_no_changes(caplog) -> None:
    tracker = Tracker(MagicMock(spec=Storage))
    tracker._storage.load_items.return_value = [_item("bad")]
    tracker._get_session = MagicMock(return_value=None)

    def stop_after_cycle(_interval):
        tracker.stop()
        return True

    tracker._stop_event.wait = stop_after_cycle
    with caplog.at_level(logging.INFO):
        tracker.run(0)
    assert "Check cycle failed" in caplog.text
    assert "No changes detected" not in caplog.text


def test_interrupted_run_does_not_log_no_changes(caplog) -> None:
    tracker = Tracker(MagicMock(spec=Storage))
    tracker.check_once = MagicMock(return_value=[])
    tracker.last_check_summary = CheckSummary(1, 1, (), interrupted=True)
    with caplog.at_level(logging.INFO):
        tracker.run(3600)
    assert "Check cycle interrupted" in caplog.text
    assert "No changes detected" not in caplog.text


def test_stop_during_item_skips_remaining_items() -> None:
    storage = MagicMock(spec=Storage)
    items = [_item("first"), _item("second")]
    storage.load_items.return_value = items
    tracker = Tracker(storage)
    entered, release = threading.Event(), threading.Event()
    client = httpx.Client()
    tracker._get_session = MagicMock(return_value=(client, None))

    def fetch(_client, upc, *_args):
        entered.set()
        assert release.wait(2)
        return _product(upc)

    with patch("krogetter.tracker.fetch_product_data", side_effect=fetch):
        worker = threading.Thread(target=tracker.check_once)
        worker.start()
        try:
            assert entered.wait(2)
            tracker.stop()
        finally:
            release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert tracker.last_check_summary == CheckSummary(1, 1, (), True)
    tracker.close()
    client.close()


def test_manual_check_serializes_behind_poll_and_refresh() -> None:
    storage = MagicMock(spec=Storage)
    item_a, item_b = _item("a"), _item("b")
    storage.load_items.return_value = [item_a]
    storage.load_history.return_value = []
    tracker = Tracker(storage)
    old, new = httpx.Client(), httpx.Client()
    close_order = []
    for name, client in (("old", old), ("new", new)):
        original_close = client.close
        client.close = lambda name=name, close=original_close: (close_order.append(name), close())[
            1
        ]
    clients = iter((old, new))
    tracker._get_browser = MagicMock(return_value=MagicMock())
    tracker.operation_lock = _ObservedLock(tracker.operation_lock)
    entered, release, manual_entered = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def fetch(client, upc, *_args):
        assert not client.is_closed
        calls.append((client, upc))
        if upc == "a":
            entered.set()
            assert release.wait(2)
            return None
        manual_entered.set()
        return _product(upc)

    errors = []
    with (
        patch("krogetter.tracker.prepare_session", side_effect=lambda *_: (next(clients), None)),
        patch("krogetter.tracker.fetch_product_data", side_effect=fetch),
    ):
        polling = threading.Thread(target=lambda: _capture(errors, tracker.check_once))
        polling.start()
        try:
            assert entered.wait(2)
            manual = threading.Thread(
                target=lambda: _capture(errors, lambda: tracker.check_item(item_b))
            )
            manual.start()
            assert tracker.operation_lock.contender.wait(2)
        finally:
            release.set()
        polling.join(2)
        manual.join(2)
    try:
        assert not polling.is_alive() and not manual.is_alive() and errors == []
        assert calls == [(old, "a"), (new, "a"), (new, "b")]
        assert old.is_closed
        assert close_order == ["old"]
    finally:
        tracker.close()
        if not old.is_closed:
            old.close()
        if not new.is_closed:
            new.close()
    assert close_order == ["old", "new"]


class _ObservedLock:
    def __init__(self, lock):
        self.lock = lock
        self.contender = threading.Event()

    def __enter__(self):
        if not self.lock.acquire(blocking=False):
            self.contender.set()
            self.lock.acquire()
        return self

    def __exit__(self, *_args):
        self.lock.release()


def test_stop_rejects_new_public_operations() -> None:
    tracker = Tracker(MagicMock(spec=Storage))
    tracker.stop()
    try:
        for operation in (
            tracker.check_once,
            lambda: tracker.check_item(_item("x")),
            lambda: tracker.fetch_product_for_item(_item("x")),
        ):
            with pytest.raises(RuntimeError, match="stopping"):
                operation()
    finally:
        tracker.close()


def test_history_write_oserror_is_failed_attempt() -> None:
    storage = MagicMock(spec=Storage)
    item = _item("write-error")
    storage.load_items.return_value = [item]
    storage.load_history.return_value = []
    storage.append_history.side_effect = OSError("disk full")
    tracker = Tracker(storage)
    client = httpx.Client()
    tracker._get_session = MagicMock(return_value=(client, None))
    try:
        with patch("krogetter.tracker.fetch_product_data", return_value=_product(item.upc)):
            assert tracker.check_once() == []
        assert tracker.last_check_summary == CheckSummary(1, 0, (item.upc,))
        assert tracker.check_item(item) == []
        assert tracker.last_check_summary == CheckSummary(1, 0, (item.upc,))
    finally:
        tracker.close()
        client.close()


def test_browser_teardown_once_on_executor_thread_and_no_post_close_submission() -> None:
    tracker = Tracker(MagicMock(spec=Storage))
    owned_thread = []

    class ContextManager:
        def __exit__(self, *_args):
            owned_thread.append(threading.current_thread().name)

    tracker._browser = object()
    tracker._browser_cm = ContextManager()
    tracker.close()
    tracker.close()
    assert len(owned_thread) == 1
    assert owned_thread[0].startswith("krogetter-browser")
    submit = MagicMock()
    tracker._browser_executor.submit = submit
    for operation in (
        lambda: tracker.check_once(),
        lambda: tracker.check_item(_item("x")),
        lambda: tracker.fetch_product_for_item(_item("x")),
    ):
        with pytest.raises(RuntimeError, match="closed"):
            operation()
    submit.assert_not_called()


def test_no_price_product_is_a_failed_attempt() -> None:
    storage = MagicMock(spec=Storage)
    item = _item("no-price")
    storage.load_items.return_value = [item]
    tracker = Tracker(storage)
    client = httpx.Client()
    tracker._get_session = MagicMock(return_value=(client, None))
    no_price = Product(
        product_id=item.upc,
        upc=item.upc,
        description="x",
        brand=None,
        size=None,
        categories=[],
        price=None,
        image_url=None,
    )
    try:
        with patch("krogetter.tracker.fetch_product_data", return_value=no_price):
            tracker.check_once()
        assert tracker.last_check_summary == CheckSummary(1, 0, (item.upc,))
    finally:
        tracker.close()
        client.close()
