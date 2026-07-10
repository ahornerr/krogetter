"""Polling tracker for checking tracked item prices."""

import logging
import time
from typing import Any

from krogetter.api.kroger_web import fetch_product as fetch_product_web
from krogetter.detector import ChangeEvent, detect_change
from krogetter.models import PriceSnapshot, TrackedItem
from krogetter.storage import Storage

logger = logging.getLogger(__name__)


def _snapshot_from_history(entry: dict) -> PriceSnapshot | None:
    """Reconstruct a PriceSnapshot from a history dict entry."""
    try:
        return PriceSnapshot(
            regular=float(entry["regular"]),
            promo=float(entry["promo"]),
            promo_description=entry.get("promo_description"),
            checked_at=entry["checked_at"],
            offer_template=entry.get("offer_template"),
            offer_start=entry.get("offer_start"),
            offer_end=entry.get("offer_end"),
            fulfillment_price_string=entry.get("fulfillment_price_string"),
            available=entry.get("available", True),  # default True for old entries
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Skipping malformed history entry: %s", exc)
        return None


class Tracker:
    """Polls tracked items for price changes using the stealth Firefox web fetcher.

    Launches a shared browser instance per polling cycle for efficiency.
    Never crashes on a single item failure — logs the error and continues.
    """

    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._browser: Any = None  # Shared browser, lazily initialized
        self._browser_cm: Any = None  # Browser context manager handle

    def _get_browser(self) -> Any:
        """Lazily initialize a shared browser instance."""
        if self._browser is None:
            from invisible_playwright import InvisiblePlaywright
            self._browser_cm = InvisiblePlaywright(headless=True)
            self._browser = self._browser_cm.__enter__()
        return self._browser

    def check_once(self) -> list[tuple[TrackedItem, list[ChangeEvent]]]:
        """Check all tracked items once.
        Returns list of (item, changes) for items with changes."""
        items = self._storage.load_items()
        if not items:
            logger.info("No tracked items to check")
            return []

        results: list[tuple[TrackedItem, list[ChangeEvent]]] = []
        try:
            for item in items:
                try:
                    changes = self.check_item(item)
                    if changes:
                        results.append((item, changes))
                except Exception:
                    logger.exception(
                        "Unexpected error checking item %s (%s)", item.upc, item.label
                    )
        finally:
            self._cleanup_browser()

        return results

    def check_item(self, item: TrackedItem) -> list[ChangeEvent]:
        """Check a single tracked item. Returns list of changes (empty if none)."""
        try:
            browser = self._get_browser()
            product = fetch_product_web(
                item.url,
                browser=browser,
                zip_code=item.zip_code,
                modality=item.modality,
                store_id=item.location_id,
            )
        except Exception as exc:
            logger.warning("Web fetch failed for %s (%s): %s", item.upc, item.label, exc)
            return []

        if product is None:
            logger.info("Could not fetch product for %s (%s)", item.upc, item.label)
            return []

        if product.price is None:
            logger.info("Product %s (%s) has no price data", item.upc, item.label)
            return []

        current = product.price

        history = self._storage.load_history(item.upc, limit=1)
        last_snapshot: PriceSnapshot | None = None
        if history:
            last_snapshot = _snapshot_from_history(history[0])

        changes = detect_change(last_snapshot, current)
        self._storage.append_history(item.upc, current)

        if changes:
            for change in changes:
                if change.field == "initial":
                    logger.info(
                        "Initial check for %s (%s): %s",
                        item.upc, item.label, change.new_value,
                    )
                elif change.is_new_sale:
                    logger.info(
                        "Sale/offer detected for %s (%s): %s",
                        item.upc, item.label, change.new_value,
                    )
                elif change.is_sale_ended:
                    logger.info(
                        "Sale/offer ended for %s (%s)", item.upc, item.label,
                    )
                else:
                    logger.info(
                        "Change for %s (%s): %s: %s -> %s",
                        item.upc, item.label, change.field,
                        change.old_value, change.new_value,
                    )

        return changes

    def run(self, interval_seconds: int = 3600) -> None:
        """Run the polling loop forever (until interrupted)."""
        logger.info("Starting tracker polling loop (interval=%ds)", interval_seconds)
        try:
            while True:
                try:
                    logger.info("Checking all tracked items...")
                    results = self.check_once()
                    if results:
                        logger.info("Found %d items with changes", len(results))
                    else:
                        logger.info("No changes detected in this cycle")
                except Exception:
                    logger.exception("Unexpected error in polling cycle")
                logger.info("Sleeping for %d seconds...", interval_seconds)
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("Tracker stopped by user")
        finally:
            self._cleanup_browser()

    def _cleanup_browser(self) -> None:
        """Close the shared browser if one was opened."""
        if self._browser is not None:
            try:
                self._browser_cm.__exit__(None, None, None)
            except Exception:
                logger.debug("Error closing browser", exc_info=True)
            self._browser = None
            self._browser_cm = None
