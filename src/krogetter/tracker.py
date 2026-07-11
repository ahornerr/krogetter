"""Polling tracker for checking tracked item prices."""

import logging
import time
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from krogetter.api.kroger_web import fetch_product_data, prepare_session
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
            inventory_level=entry.get("inventory_level"),  # None for old entries
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Skipping malformed history entry: %s", exc)
        return None


def _store_key(item: TrackedItem) -> tuple[str, str, str, str | None]:
    """Build a cache key from a tracked item's store config."""
    parsed = urlparse(item.url)
    homepage = f"{parsed.scheme}://{parsed.netloc}/"
    return (homepage, item.zip_code, item.modality, item.location_id)


class Tracker:
    """Polls tracked items for price changes using the stealth Firefox web fetcher.

    The browser is kept alive across polling cycles. Sessions (playwright page +
    LAF headers + context) are cached per store config and only re-created when
    the page context expires.
    """

    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._browser: Any = None  # Shared browser, lazily initialized
        self._browser_cm: Any = None  # Browser context manager handle
        # Cached sessions: store_key -> (page, laf_headers, context)
        self._sessions: dict[tuple[str, str, str, str | None], tuple[Any, dict[str, str] | None, Any]] = {}

    def _get_browser(self) -> Any:
        """Lazily initialize a shared browser instance."""
        if self._browser is None:
            from invisible_playwright import InvisiblePlaywright
            self._browser_cm = InvisiblePlaywright(headless=True)
            self._browser = self._browser_cm.__enter__()
        return self._browser

    def _get_session(
        self, key: tuple[str, str, str, str | None], force_refresh: bool = False
    ) -> tuple[Any, dict[str, str] | None, Any] | None:
        """Get or create a cached session for the given store config.

        On first call, warms up the browser and creates the page + context.
        On subsequent calls, returns the cached page.

        Args:
            key: (homepage, zip_code, modality, store_id)
            force_refresh: If True, close the old page and context and create
                          new ones (used when API calls fail with 403/429).
        """
        if force_refresh and key in self._sessions:
            old_page, _, old_context = self._sessions.pop(key)
            try:
                old_page.close()
            except Exception:
                pass
            try:
                old_context.close()
            except Exception:
                pass
            logger.info("Refreshing expired session for %s", key[0])

        if key in self._sessions:
            return self._sessions[key]

        # Create new session — this is the only place the browser is used
        homepage, zip_code, modality, store_id = key
        try:
            browser = self._get_browser()
            page, laf_headers, context = prepare_session(
                browser, homepage, zip_code, modality, store_id
            )
        except Exception:
            logger.exception("Session setup failed for %s", homepage)
            return None

        self._sessions[key] = (page, laf_headers, context)
        return page, laf_headers, context

    def check_once(self) -> list[tuple[TrackedItem, list[ChangeEvent]]]:
        """Check all tracked items once.

        Groups items by store config and reuses cached sessions. Only
        re-warms the browser when a session's page context has expired.

        Returns list of (item, changes) for items with changes.
        """
        items = self._storage.load_items()
        if not items:
            logger.info("No tracked items to check")
            return []

        # Group items by store config
        groups: dict[tuple[str, str, str, str | None], list[TrackedItem]] = defaultdict(list)
        for item in items:
            groups[_store_key(item)].append(item)

        results: list[tuple[TrackedItem, list[ChangeEvent]]] = []
        for key, group_items in groups.items():
            homepage, zip_code, modality, store_id = key
            logger.info(
                "Checking %d item(s) for %s (zip=%s, modality=%s, store=%s)",
                len(group_items), homepage, zip_code, modality, store_id,
            )

            session = self._get_session(key)
            if session is None:
                continue

            page, laf_headers, context = session
            for item in group_items:
                try:
                    changes = self._check_item_with_session(
                        item, page, laf_headers, modality, key
                    )
                    if changes:
                        results.append((item, changes))
                except Exception:
                    logger.exception(
                        "Unexpected error checking item %s (%s)",
                        item.upc, item.label,
                    )

        return results

    def _check_item_with_session(
        self, item: TrackedItem, page: Any, laf_headers: dict[str, str] | None,
        modality: str, session_key: tuple[str, str, str, str | None] | None = None,
    ) -> list[ChangeEvent]:
        """Check a single item using an existing session.

        If the API returns an error (cookies expired), refreshes the session
        and retries once.
        """
        try:
            product = fetch_product_data(page, item.upc, laf_headers, modality)
        except Exception as exc:
            # Check if this is an auth failure we can retry
            if session_key and _is_auth_error(exc):
                logger.info("Session expired for %s, refreshing...", item.upc)
                session = self._get_session(session_key, force_refresh=True)
                if session is None:
                    return []
                page, laf_headers, _ = session
                try:
                    product = fetch_product_data(page, item.upc, laf_headers, modality)
                except Exception as exc2:
                    logger.warning("Web fetch failed for %s (%s): %s", item.upc, item.label, exc2)
                    return []
            else:
                logger.warning("Web fetch failed for %s (%s): %s", item.upc, item.label, exc)
                return []

        if product is None:
            # Product API returned non-200 — might be expired cookies
            if session_key:
                logger.info("Product fetch returned None for %s, refreshing session...", item.upc)
                session = self._get_session(session_key, force_refresh=True)
                if session is not None:
                    page, laf_headers, _ = session
                    try:
                        product = fetch_product_data(page, item.upc, laf_headers, modality)
                    except Exception:
                        pass

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

    def check_item(self, item: TrackedItem) -> list[ChangeEvent]:
        """Check a single tracked item. Returns list of changes (empty if none).

        Uses cached sessions when available. For batch checking, use
        check_once() instead.
        """
        key = _store_key(item)
        session = self._get_session(key)
        if session is None:
            return []

        page, laf_headers, context = session
        return self._check_item_with_session(item, page, laf_headers, item.modality, key)

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
            self._cleanup()

    def _cleanup(self) -> None:
        """Close all cached sessions (pages + contexts) and the browser."""
        for page, _, context in self._sessions.values():
            try:
                page.close()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
        self._sessions.clear()

        if self._browser is not None:
            try:
                self._browser_cm.__exit__(None, None, None)
            except Exception:
                logger.debug("Error closing browser", exc_info=True)
            self._browser = None
            self._browser_cm = None

    # Backwards compat
    def _cleanup_browser(self) -> None:
        """Close the shared browser if one was opened."""
        self._cleanup()


def _is_auth_error(exc: Exception) -> bool:
    """Check if an exception is likely caused by expired Akamai cookies."""
    msg = str(exc).lower()
    return any(s in msg for s in ("403", "429", "forbidden", "access denied", "timeout"))
