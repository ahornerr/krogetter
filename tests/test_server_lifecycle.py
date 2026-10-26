"""Server shutdown lifecycle coverage."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from krogetter.models import PriceSnapshot, Product, TrackedItem
from krogetter.server.app import create_app
from krogetter.storage import Storage
from krogetter.tracker import Tracker


def test_shutdown_stops_joins_and_closes_off_event_loop(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []

    class Poller:
        def join(self) -> None:
            calls.append(("join", threading.get_ident()))

    with (
        patch("krogetter.server.app._start_polling_thread", return_value=Poller()),
        patch(
            "krogetter.server.app.Tracker.stop",
            side_effect=lambda: calls.append(("stop", threading.get_ident())),
        ),
        patch(
            "krogetter.server.app.Tracker.close",
            side_effect=lambda: calls.append(("close", threading.get_ident())),
        ),
        TestClient(create_app(tmp_path)),
    ):
        pass

    assert [name for name, _ in calls] == ["stop", "join", "close"]
    assert calls[1][1] != calls[0][1]
    assert calls[2][1] != calls[0][1]


def test_shutdown_is_safe_without_polling_thread(tmp_path: Path) -> None:
    with (
        patch("krogetter.server.app._start_polling_thread", return_value=None),
        patch("krogetter.server.app.Tracker.stop") as stop,
        patch("krogetter.server.app.Tracker.close") as close,
        TestClient(create_app(tmp_path)),
    ):
        pass
    stop.assert_called_once()
    close.assert_called_once()


def test_real_idle_poller_stops_promptly(tmp_path: Path) -> None:
    entered_wait = threading.Event()
    trackers: list[Tracker] = []
    original_run = Tracker.run

    def observed_run(tracker: Tracker, interval_seconds: int = 3600) -> None:
        trackers.append(tracker)
        wait = tracker._stop_event.wait

        def observed_wait(timeout=None):
            entered_wait.set()
            return wait(timeout)

        tracker._stop_event.wait = observed_wait
        original_run(tracker, interval_seconds)

    with (
        patch.object(Tracker, "run", observed_run),
        TestClient(create_app(tmp_path, poll_interval=3600)) as client,
    ):
        assert entered_wait.wait(3)
        poller = client.app.state.polling_thread

    assert not poller.is_alive()
    assert trackers[0]._closed


def test_shutdown_drains_active_fetch_before_closing_client(tmp_path: Path) -> None:
    entered_fetch = threading.Event()
    release_fetch = threading.Event()
    shutdown_started = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []
    clients: list[httpx.Client] = []
    upc = "0004900004825"
    skipped_upc = "0004900004832"
    storage = Storage(tmp_path)
    storage.add_item(
        TrackedItem(
            url=f"https://www.kingsoopers.com/p/coke/{upc}",
            upc=upc,
            label="Coke",
            added_at="2026-01-01",
        )
    )
    storage.add_item(
        TrackedItem(
            url=f"https://www.kingsoopers.com/p/coke/{skipped_upc}",
            upc=skipped_upc,
            label="Coke two",
            added_at="2026-01-01",
        )
    )
    app = create_app(tmp_path, poll_interval=3600)

    def fetch(*args, **kwargs):
        assert args[1] == upc
        entered_fetch.set()
        if not release_fetch.wait(5):
            raise TimeoutError("fetch release timed out")
        return Product(
            product_id=upc,
            upc=upc,
            description="Coke",
            brand="Coke",
            size="12pk",
            categories=[],
            price=PriceSnapshot(
                regular=10.0,
                promo=0.0,
                promo_description=None,
                checked_at="2026-07-10T00:00:00+00:00",
            ),
            image_url=None,
        )

    def prepare(*args, **kwargs):
        client = httpx.Client()
        clients.append(client)
        return client, None

    original_stop = Tracker.stop

    def observed_stop(tracker: Tracker) -> None:
        original_stop(tracker)
        shutdown_started.set()

    async def lifespan_runner():
        try:
            async with app.router.lifespan_context(app):
                if not entered_fetch.wait(5):
                    raise TimeoutError("poll fetch did not start")
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    with (
        patch.object(Tracker, "_get_browser", return_value=object()),
        patch.object(Tracker, "stop", observed_stop),
        patch("krogetter.tracker.prepare_session", side_effect=prepare),
        patch("krogetter.tracker.fetch_product_data", side_effect=fetch),
    ):
        thread = threading.Thread(target=lambda: asyncio.run(lifespan_runner()))
        thread.start()
        try:
            assert shutdown_started.wait(5)
            assert clients and not clients[0].is_closed
            assert not finished.is_set()
        finally:
            release_fetch.set()
        assert finished.wait(5)
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
    assert clients[0].is_closed
    assert not app.state.polling_thread.is_alive()
