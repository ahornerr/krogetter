"""FastAPI application for the krogetter API server."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder

from krogetter.detector import ChangeEvent
from krogetter.models import PriceSnapshot, TrackedItem
from krogetter.server.schemas import (
    ChangeEventResponse,
    CheckResultResponse,
    HealthResponse,
    ItemCreateRequest,
    ItemResponse,
    ItemWithLatestResponse,
    SnapshotResponse,
)
from krogetter.storage import Storage
from krogetter.tracker import Tracker, _snapshot_from_history
from krogetter.url import extract_upc_or_passthrough

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Helper functions
# ------------------------------------------------------------------ #


def _snapshot_to_response(snap: PriceSnapshot) -> SnapshotResponse:
    """Convert a PriceSnapshot to a SnapshotResponse, including computed properties."""
    return SnapshotResponse(
        regular=snap.regular,
        promo=snap.promo,
        promo_description=snap.promo_description,
        checked_at=snap.checked_at,
        offer_template=snap.offer_template,
        offer_start=snap.offer_start,
        offer_end=snap.offer_end,
        fulfillment_price_string=snap.fulfillment_price_string,
        available=snap.available,
        inventory_level=snap.inventory_level,
        current_price=snap.current_price,
        effective_unit_price=snap.effective_unit_price,
        is_on_sale=snap.is_on_sale,
        has_offer=snap.has_offer,
        savings=snap.savings,
        savings_percent=snap.savings_percent,
        synthetic_description=snap.synthetic_description,
    )


def _item_to_response(item: TrackedItem) -> ItemResponse:
    """Convert a TrackedItem to an ItemResponse."""
    return ItemResponse(
        upc=item.upc,
        label=item.label,
        url=item.url,
        zip_code=item.zip_code,
        modality=item.modality,
        location_id=item.location_id,
        chain=item.chain,
        added_at=item.added_at,
    )


def _history_to_snapshot(entry: dict) -> PriceSnapshot | None:
    """Convert a history dict entry to a PriceSnapshot. Reuses tracker utility."""
    return _snapshot_from_history(entry)


def _change_to_response(change: ChangeEvent) -> ChangeEventResponse:
    """Convert a ChangeEvent to a ChangeEventResponse."""
    return ChangeEventResponse(
        field=change.field,
        old_value=change.old_value,
        new_value=change.new_value,
        is_new_sale=change.is_new_sale,
        is_sale_ended=change.is_sale_ended,
    )


# ------------------------------------------------------------------ #
#  Polling thread
# ------------------------------------------------------------------ #


def _start_polling_thread(tracker: Tracker, interval: int) -> threading.Thread:
    """Start a background daemon thread that runs tracker.run() with the given interval."""

    def _poll() -> None:
        tracker.run(interval_seconds=interval)

    thread = threading.Thread(target=_poll, daemon=True, name="krogetter-poller")
    thread.start()
    return thread


# ------------------------------------------------------------------ #
#  App factory
# ------------------------------------------------------------------ #


def create_app(data_dir: str | Path, poll_interval: int = 3600) -> FastAPI:
    """Create a FastAPI application configured with storage and tracker.

    Args:
        data_dir: Path to the data directory for persistence.
        poll_interval: Seconds between automatic polling cycles.

    Returns:
        A configured FastAPI application instance.
    """
    data_dir = Path(data_dir)
    storage = Storage(data_dir)
    tracker = Tracker(storage=storage)
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.polling_thread = _start_polling_thread(tracker, poll_interval)
        try:
            yield
        finally:
            tracker._cleanup_browser()

    app = FastAPI(title="krogetter", version="0.1.0", lifespan=lifespan)

    # ------------------------------------------------------------------ #
    #  Routes
    # ------------------------------------------------------------------ #

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/items", response_model=list[ItemWithLatestResponse])
    def list_items() -> list[dict[str, Any]]:
        items = storage.load_items()
        results: list[dict[str, Any]] = []
        for item in items:
            item_data = jsonable_encoder(_item_to_response(item))
            history = storage.load_history(item.upc, limit=1)
            latest: SnapshotResponse | None = None
            if history:
                snap = _history_to_snapshot(history[0])
                if snap is not None:
                    latest = _snapshot_to_response(snap)
            item_data["latest"] = jsonable_encoder(latest) if latest else None
            results.append(item_data)
        return results

    @app.post("/api/items", response_model=ItemResponse, status_code=201)
    def create_item(body: ItemCreateRequest) -> dict[str, Any]:
        # Extract UPC
        try:
            upc = extract_upc_or_passthrough(body.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        modality = "DELIVERY" if body.delivery else "PICKUP"

        # Create a temporary item to fetch product data
        item = TrackedItem(
            url=body.url,
            upc=upc,
            label=upc,  # placeholder — will be replaced from API
            location_id=body.store_id,
            zip_code=body.zip_code or "",
            modality=modality,
            added_at=datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        )

        # Fetch product from API to get the real label and initial price
        with lock:
            product = tracker.fetch_product_for_item(item)

        if product is None:
            raise HTTPException(
                status_code=422,
                detail=f"Could not fetch product data for UPC {upc}. "
                       "Check the URL is correct and the product exists.",
            )

        # Use the product description as the label
        label = product.description
        if product.brand:
            label = f"{product.brand} {product.description}"
        item.label = label

        # Store the item
        try:
            storage.add_item(item)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # Store the initial price snapshot
        if product.price:
            storage.append_history(item.upc, product.price)
            logger.info("Initial check for %s: %s", item.upc, label)

        return jsonable_encoder(_item_to_response(item))

    @app.delete("/api/items/{upc}", status_code=204)
    def delete_item(upc: str) -> None:
        removed = storage.remove_item(upc)
        if not removed:
            raise HTTPException(status_code=404, detail=f"No tracked item found with UPC: {upc}")

    @app.post("/api/items/{upc}/check", response_model=CheckResultResponse)
    def check_item(upc: str) -> dict[str, Any]:
        items = storage.load_items()
        matching = [item for item in items if item.upc == upc]
        if not matching:
            raise HTTPException(status_code=404, detail=f"No tracked item found with UPC: {upc}")

        item = matching[0]

        with lock:
            changes = tracker.check_item(item)

        # Fetch latest history entry after check
        history = storage.load_history(upc, limit=1)
        latest: SnapshotResponse | None = None
        if history:
            snap = _history_to_snapshot(history[0])
            if snap is not None:
                latest = _snapshot_to_response(snap)

        return {
            "upc": upc,
            "changes": jsonable_encoder([_change_to_response(c) for c in changes]),
            "latest": jsonable_encoder(latest) if latest else None,
        }

    @app.post("/api/check", response_model=list[CheckResultResponse])
    def check_all() -> list[dict[str, Any]]:
        with lock:
            results = tracker.check_once()

        output: list[dict[str, Any]] = []
        for item, changes in results:
            history = storage.load_history(item.upc, limit=1)
            latest: SnapshotResponse | None = None
            if history:
                snap = _history_to_snapshot(history[0])
                if snap is not None:
                    latest = _snapshot_to_response(snap)
            output.append({
                "upc": item.upc,
                "changes": jsonable_encoder([_change_to_response(c) for c in changes]),
                "latest": jsonable_encoder(latest) if latest else None,
            })
        return output

    @app.get("/api/items/{upc}/latest", response_model=SnapshotResponse)
    def get_latest(upc: str) -> dict[str, Any]:
        history = storage.load_history(upc, limit=1)
        if not history:
            raise HTTPException(status_code=404, detail=f"No history found for UPC: {upc}")
        snap = _history_to_snapshot(history[0])
        if snap is None:
            raise HTTPException(status_code=404, detail=f"No valid history found for UPC: {upc}")
        return jsonable_encoder(_snapshot_to_response(snap))

    @app.get("/api/items/{upc}/history", response_model=list[SnapshotResponse])
    def get_history(upc: str, limit: int = 100) -> list[dict[str, Any]]:
        history = storage.load_history(upc, limit=limit)
        results: list[dict[str, Any]] = []
        for entry in history:
            snap = _history_to_snapshot(entry)
            if snap is not None:
                results.append(jsonable_encoder(_snapshot_to_response(snap)))
        return results

    return app
