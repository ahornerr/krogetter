"""Tests for the FastAPI server using TestClient."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from krogetter.server.app import create_app


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Temporary data directory for tests."""
    return tmp_path / "krogetter_data"


@pytest.fixture
def client(data_dir: Path):
    """Create a TestClient with a fresh app backed by a temp dir."""
    with (
        patch("krogetter.server.app._start_polling_thread", return_value=None),
        patch("krogetter.tracker.Tracker.check_item", return_value=[]),
        patch("krogetter.tracker.Tracker.check_once", return_value=[]),
    ):
        app = create_app(data_dir=data_dir, poll_interval=99999)
        yield TestClient(app)


class TestHealth:
    """Tests for GET /api/health."""

    def test_health_returns_ok(self, client: TestClient) -> None:
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


class TestItems:
    """Tests for item CRUD endpoints."""

    def test_create_item_with_url(self, client: TestClient) -> None:
        response = client.post(
            "/api/items",
            json={
                "url": "https://www.kingsoopers.com/p/coca-cola/0004900004825",
                "label": "Coke Classic",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["upc"] == "0004900004825"
        assert "Coke" in data["label"]
        assert data["url"] == "https://www.kingsoopers.com/p/coca-cola/0004900004825"

    def test_create_item_with_bare_upc(self, client: TestClient) -> None:
        response = client.post(
            "/api/items",
            json={
                "url": "0004900004825",
                "label": "Coke Classic",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["upc"] == "0004900004825"
        assert data["label"] == "Coke Classic"

    def test_create_item_duplicate_upc(self, client: TestClient) -> None:
        url = "https://www.kingsoopers.com/p/coca-cola/0004900004825"
        client.post("/api/items", json={"url": url, "label": "First"})

        response = client.post("/api/items", json={"url": url, "label": "Second"})
        assert response.status_code == 409
        assert "already being tracked" in response.json()["detail"]

    def test_create_item_bad_url(self, client: TestClient) -> None:
        response = client.post("/api/items", json={"url": "not-a-url"})
        assert response.status_code == 400

    def test_list_items_empty(self, client: TestClient) -> None:
        response = client.get("/api/items")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_list_items_with_one(self, client: TestClient) -> None:
        client.post(
            "/api/items",
            json={"url": "https://www.kingsoopers.com/p/coca-cola/0004900004825"},
        )

        response = client.get("/api/items")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["upc"] == "0004900004825"
        assert data[0]["latest"] is None  # no history yet

    def test_list_items_with_history(self, client: TestClient, data_dir: Path) -> None:
        """Item with history should show latest snapshot."""
        client.post(
            "/api/items",
            json={"url": "https://www.kingsoopers.com/p/coca-cola/0004900004825"},
        )

        # Manually append history via storage
        from krogetter.models import PriceSnapshot
        from krogetter.storage import Storage

        storage = Storage(data_dir)
        snap = PriceSnapshot(
            regular=11.99,
            promo=8.99,
            promo_description=None,
            checked_at="2026-07-09T12:00:00+00:00",
        )
        storage.append_history("0004900004825", snap)

        response = client.get("/api/items")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        latest = data[0]["latest"]
        assert latest is not None
        assert latest["regular"] == 11.99
        assert latest["promo"] == 8.99
        assert latest["is_on_sale"] is True
        assert latest["current_price"] == 8.99

    def test_delete_item(self, client: TestClient) -> None:
        client.post(
            "/api/items",
            json={"url": "https://www.kingsoopers.com/p/coca-cola/0004900004825"},
        )

        response = client.delete("/api/items/0004900004825")
        assert response.status_code == 204

        # Should be gone from list
        list_response = client.get("/api/items")
        assert list_response.json() == []

    def test_delete_nonexistent_item(self, client: TestClient) -> None:
        response = client.delete("/api/items/0000000000000")
        assert response.status_code == 404


class TestLatest:
    """Tests for GET /api/items/{upc}/latest."""

    def test_latest_no_history(self, client: TestClient) -> None:
        client.post(
            "/api/items",
            json={"url": "https://www.kingsoopers.com/p/coca-cola/0004900004825"},
        )

        response = client.get("/api/items/0004900004825/latest")
        assert response.status_code == 404

    def test_latest_unknown_upc(self, client: TestClient) -> None:
        response = client.get("/api/items/9999999999999/latest")
        assert response.status_code == 404

    def test_latest_with_history(self, client: TestClient, data_dir: Path) -> None:
        from krogetter.models import PriceSnapshot
        from krogetter.storage import Storage

        storage = Storage(data_dir)
        snap = PriceSnapshot(
            regular=11.99,
            promo=8.99,
            promo_description=None,
            checked_at="2026-07-09T12:00:00+00:00",
        )
        storage.append_history("0004900004825", snap)

        response = client.get("/api/items/0004900004825/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["regular"] == 11.99
        assert data["promo"] == 8.99
        assert data["is_on_sale"] is True


class TestHistory:
    """Tests for GET /api/items/{upc}/history."""

    def test_history_empty(self, client: TestClient) -> None:
        client.post(
            "/api/items",
            json={"url": "https://www.kingsoopers.com/p/coca-cola/0004900004825"},
        )

        response = client.get("/api/items/0004900004825/history")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_history_with_entries(self, client: TestClient, data_dir: Path) -> None:
        from krogetter.models import PriceSnapshot
        from krogetter.storage import Storage

        storage = Storage(data_dir)
        snap1 = PriceSnapshot(
            regular=11.99,
            promo=0.0,
            promo_description=None,
            checked_at="2026-07-08T12:00:00+00:00",
        )
        snap2 = PriceSnapshot(
            regular=11.99,
            promo=8.99,
            promo_description=None,
            checked_at="2026-07-09T12:00:00+00:00",
        )
        storage.append_history("0004900004825", snap1)
        storage.append_history("0004900004825", snap2)

        response = client.get("/api/items/0004900004825/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Most recent first
        assert data[0]["checked_at"] == "2026-07-09T12:00:00+00:00"
        assert data[1]["checked_at"] == "2026-07-08T12:00:00+00:00"

    def test_history_with_limit(self, client: TestClient, data_dir: Path) -> None:
        from krogetter.models import PriceSnapshot
        from krogetter.storage import Storage

        storage = Storage(data_dir)
        for i in range(5):
            snap = PriceSnapshot(
                regular=float(10 + i),
                promo=0.0,
                promo_description=None,
                checked_at=f"2026-07-0{i}T12:00:00+00:00",
            )
            storage.append_history("0004900004825", snap)

        response = client.get("/api/items/0004900004825/history?limit=3")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3


class TestCheck:
    """Tests for POST /api/items/{upc}/check and POST /api/check."""

    def test_check_item_not_found(self, client: TestClient) -> None:
        response = client.post("/api/items/0004900004825/check")
        assert response.status_code == 404

    def test_check_item_mocked(self, client: TestClient) -> None:
        """Check endpoint with mocked tracker returns changes."""
        from krogetter.detector import ChangeEvent

        # Add an item first
        client.post(
            "/api/items",
            json={"url": "https://www.kingsoopers.com/p/coca-cola/0004900004825"},
        )

        change = ChangeEvent(
            field="initial",
            old_value="no data",
            new_value="Save $3.00 (25.0% off)",
            is_new_sale=True,
            is_sale_ended=False,
        )

        # Manually append history so latest works
        from krogetter.storage import Storage

        storage = Storage(Path.cwd() / "ignored")  # won't be used
        storage.append_history = MagicMock()  # type: ignore[method-assign]

        with patch("krogetter.server.app.Tracker.check_item", return_value=[change]):
            response = client.post("/api/items/0004900004825/check")

        assert response.status_code == 200
        data = response.json()
        assert data["upc"] == "0004900004825"
        assert len(data["changes"]) == 1
        assert data["changes"][0]["is_new_sale"] is True
        assert data["changes"][0]["field"] == "initial"

    def test_check_all_mocked(self, client: TestClient) -> None:
        """Check all endpoint with mocked tracker."""
        from krogetter.detector import ChangeEvent
        from krogetter.models import TrackedItem

        # Add an item
        client.post(
            "/api/items",
            json={"url": "https://www.kingsoopers.com/p/coca-cola/0004900004825"},
        )

        item = TrackedItem(
            url="https://www.kingsoopers.com/p/coca-cola/0004900004825",
            upc="0004900004825",
            label="Coke Classic",
            added_at="2026-07-09T00:00:00+00:00",
        )
        change = ChangeEvent(
            field="initial",
            old_value="no data",
            new_value="on sale",
            is_new_sale=True,
            is_sale_ended=False,
        )

        with patch(
            "krogetter.server.app.Tracker.check_once",
            return_value=[(item, [change])],
        ):
            response = client.post("/api/check")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["upc"] == "0004900004825"
        assert len(data[0]["changes"]) == 1
