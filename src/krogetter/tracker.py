"""Polling tracker for checking tracked item prices."""

import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from krogetter.api.kroger_web import fetch_product_data, prepare_session
from krogetter.detector import ChangeEvent, detect_change
from krogetter.models import Offer, PriceSnapshot, Product, TrackedItem
from krogetter.storage import Storage

logger = logging.getLogger(__name__)


class TrackerStoppedError(RuntimeError):
    """Raised when a new public tracker operation starts after stop was requested."""


@dataclass(frozen=True)
class CheckSummary:
    """Counts exclude skipped work; attempted is succeeded plus failed."""
    attempted: int = 0
    succeeded: int = 0
    failed_upcs: tuple[str, ...] = ()
    interrupted: bool = False

    @property
    def failed(self) -> int:
        return len(self.failed_upcs)


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
            offers=[
                Offer(
                    description=o.get("description"),
                    template=o.get("template"),
                    start=o.get("start"),
                    end=o.get("end"),
                )
                for o in entry.get("offers", [])
            ],
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

    The browser is kept alive across polling cycles. Sessions (httpx client +
    LAF headers) are cached per store config and only re-created when cookies
    expire (API returns 403/429).
    """

    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._browser: Any = None  # Shared browser, lazily initialized
        self._browser_cm: Any = None  # Browser context manager handle
        # Cached sessions: store_key -> (httpx.Client, laf_headers)
        self._sessions: dict[tuple[str, str, str, str | None], tuple[httpx.Client, dict[str, str] | None]] = {}
        # Playwright's sync API is not thread-safe: the browser must be created
        # and used from a single thread.  A single-worker executor serializes
        # all browser operations (prepare_session) onto one dedicated thread,
        # regardless of whether the caller is the polling thread or a FastAPI
        # thread-pool worker.
        self._browser_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="krogetter-browser"
        )
        self._sessions_lock = threading.Lock()
        self.operation_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._closed = False
        self.last_check_summary = CheckSummary()

    def _get_browser(self) -> Any:
        """Lazily initialize a shared browser instance."""
        if self._browser is None:
            from invisible_playwright import InvisiblePlaywright
            self._browser_cm = InvisiblePlaywright(headless=True)
            self._browser = self._browser_cm.__enter__()
        return self._browser

    def _get_session(
        self, key: tuple[str, str, str, str | None], force_refresh: bool = False
    ) -> tuple[httpx.Client, dict[str, str] | None] | None:
        """Get or create a cached session for the given store config.

        On first call, warms up the browser and creates the httpx client.
        On subsequent calls, returns the cached client.

        Args:
            key: (homepage, zip_code, modality, store_id)
            force_refresh: If True, close the old client and create a new one
                          (used when API calls fail with 403/429).
        """
        self._ensure_open()
        with self._sessions_lock:
            if force_refresh and key in self._sessions:
                old_client, _ = self._sessions.pop(key)
                try:
                    old_client.close()
                except Exception:
                    pass
                logger.info("Refreshing expired session for %s", key[0])

            if key in self._sessions:
                return self._sessions[key]

        # prepare_session touches the Playwright browser, whose sync API is
        # not thread-safe.  Run it on the dedicated browser thread so that
        # browser creation and all new_context() calls happen on one thread.
        result = self._browser_executor.submit(self._create_session, key).result()

        if result is None:
            return None

        with self._sessions_lock:
            # Another thread may have created the same session while we waited.
            if key in self._sessions:
                client, _ = result
                try:
                    client.close()
                except Exception:
                    pass
                return self._sessions[key]
            self._sessions[key] = result

        return result

    def _create_session(
        self, key: tuple[str, str, str, str | None]
    ) -> tuple[httpx.Client, dict[str, str] | None] | None:
        """Create a new session on the browser thread.

        Playwright's sync API binds the browser to the thread that created it.
        This method must run inside ``_browser_executor`` so that every
        ``browser.new_context()`` call happens on the same thread.
        """
        homepage, zip_code, modality, store_id = key
        try:
            browser = self._get_browser()
            client, laf_headers = prepare_session(
                browser, homepage, zip_code, modality, store_id
            )
        except Exception:
            logger.exception("Session setup failed for %s", homepage)
            return None
        return (client, laf_headers)

    def check_once(self) -> list[tuple[TrackedItem, list[ChangeEvent]]]:
        """Check all tracked items and return only items with detected changes."""
        with self.operation_lock:
            self._ensure_open()
            self._ensure_running()
            return self._check_once()

    def _check_once(self) -> list[tuple[TrackedItem, list[ChangeEvent]]]:
        """Check all tracked items once.

        Groups items by store config and reuses cached sessions. Only
        re-warms the browser when a session's cookies have expired.

        Returns list of (item, changes) for items with changes.
        """
        items = self._storage.load_items()
        if not items:
            logger.info("No tracked items to check")
            self.last_check_summary = CheckSummary()
            return []

        # Group items by store config
        groups: dict[tuple[str, str, str, str | None], list[TrackedItem]] = defaultdict(list)
        for item in items:
            groups[_store_key(item)].append(item)

        results: list[tuple[TrackedItem, list[ChangeEvent]]] = []
        attempted = succeeded = 0
        failed_upcs: list[str] = []
        interrupted = False
        for key, group_items in groups.items():
            if self._stop_event.is_set():
                interrupted = True
                break
            homepage, zip_code, modality, store_id = key
            logger.info(
                "Checking %d item(s) for %s (zip=%s, modality=%s, store=%s)",
                len(group_items), homepage, zip_code, modality, store_id,
            )

            session = self._get_session(key)
            if session is None:
                attempted += len(group_items)
                failed_upcs.extend(item.upc for item in group_items)
                continue
            for item in group_items:
                if self._stop_event.is_set():
                    interrupted = True
                    break
                attempted += 1
                try:
                    session = self._get_session(key)
                    if session is None:
                        failed_upcs.append(item.upc)
                        continue
                    changes, ok = self._check_item_outcome(item, *session, modality, key)
                    if ok:
                        succeeded += 1
                    else:
                        failed_upcs.append(item.upc)
                    if changes:
                        results.append((item, changes))
                except Exception:
                    failed_upcs.append(item.upc)
                    logger.exception(
                        "Unexpected error checking item %s (%s)",
                        item.upc, item.label,
                    )

        self.last_check_summary = CheckSummary(
            attempted, succeeded, tuple(failed_upcs), interrupted
        )
        return results

    def _check_item_with_session(
        self, item: TrackedItem, client: httpx.Client, laf_headers: dict[str, str] | None,
        modality: str, session_key: tuple[str, str, str, str | None] | None = None,
    ) -> list[ChangeEvent]:
        """Check a single item using an existing session.

        If the API returns 403/429 (cookies expired), refreshes the session
        and retries once.
        """
        changes, _ = self._check_item_outcome(item, client, laf_headers, modality, session_key)
        return changes

    def _check_item_outcome(
        self, item: TrackedItem, client: httpx.Client, laf_headers: dict[str, str] | None,
        modality: str, session_key: tuple[str, str, str, str | None] | None = None,
    ) -> tuple[list[ChangeEvent], bool]:
        try:
            product = fetch_product_data(client, item.upc, laf_headers, modality)
        except Exception as exc:
            # Check if this is an auth failure we can retry
            if session_key and _is_auth_error(exc):
                logger.info("Session expired for %s, refreshing...", item.upc)
                session = self._get_session(session_key, force_refresh=True)
                if session is None:
                    return [], False
                client, laf_headers = session
                try:
                    product = fetch_product_data(client, item.upc, laf_headers, modality)
                except Exception as exc2:
                    logger.warning("Web fetch failed for %s (%s): %s", item.upc, item.label, exc2)
                    return [], False
            else:
                logger.warning("Web fetch failed for %s (%s): %s", item.upc, item.label, exc)
                return [], False

        if product is None:
            # Product API returned non-200 — might be expired cookies
            if session_key:
                logger.info("Product fetch returned None for %s, refreshing session...", item.upc)
                session = self._get_session(session_key, force_refresh=True)
                if session is not None:
                    client, laf_headers = session
                    try:
                        product = fetch_product_data(client, item.upc, laf_headers, modality)
                    except Exception:
                        pass

        if product is None:
            logger.info("Could not fetch product for %s (%s)", item.upc, item.label)
            return [], False

        if product.price is None:
            logger.info("Product %s (%s) has no price data", item.upc, item.label)
            return [], False

        current = product.price

        history = self._storage.load_history(item.upc, limit=1)
        last_snapshot: PriceSnapshot | None = None
        if history:
            last_snapshot = _snapshot_from_history(history[0])

        changes = detect_change(last_snapshot, current)
        try:
            self._storage.append_history(item.upc, current)
        except OSError:
            logger.warning("Failed to persist history for %s", item.upc, exc_info=True)
            return [], False

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

        return changes, True

    def fetch_product_for_item(self, item: TrackedItem) -> Product | None:
        """Fetch product data for a new item without storing it.

        Used by the server's create_item endpoint to get the product
        description and initial price before storing the item.
        """
        with self.operation_lock:
            self._ensure_open()
            self._ensure_running()
            return self._fetch_product_for_item(item)

    def _fetch_product_for_item(self, item: TrackedItem) -> Product | None:
        key = _store_key(item)
        session = self._get_session(key)
        if session is None:
            return None
        client, laf_headers = session
        try:
            product = fetch_product_data(client, item.upc, laf_headers, item.modality)
        except Exception:
            product = None

        # If fetch failed (403/429), refresh session and retry
        if product is None:
            logger.info("Product fetch failed for %s, refreshing session...", item.upc)
            session = self._get_session(key, force_refresh=True)
            if session is None:
                return None
            client, laf_headers = session
            try:
                return fetch_product_data(client, item.upc, laf_headers, item.modality)
            except Exception:
                logger.warning("Product fetch failed for %s after refresh", item.upc, exc_info=True)
                return None

        return product

    def check_item(self, item: TrackedItem) -> list[ChangeEvent]:
        """Check a single tracked item. Returns list of changes (empty if none).

        Uses cached sessions when available. For batch checking, use
        check_once() instead.
        """
        with self.operation_lock:
            self._ensure_open()
            self._ensure_running()
            try:
                key = _store_key(item)
                session = self._get_session(key)
                if session is None:
                    self.last_check_summary = CheckSummary(1, 0, (item.upc,))
                    return []
                changes, ok = self._check_item_outcome(item, *session, item.modality, key)
                self.last_check_summary = CheckSummary(1, int(ok), () if ok else (item.upc,))
                return changes
            except Exception:
                self.last_check_summary = CheckSummary(1, 0, (item.upc,))
                raise

    def run(self, interval_seconds: int = 3600) -> None:
        """Poll until stopped; a stop during a cycle drains only its active item."""
        logger.info("Starting tracker polling loop (interval=%ds)", interval_seconds)
        try:
            while not self._stop_event.is_set():
                summary = None
                try:
                    logger.info("Checking all tracked items...")
                    with self.operation_lock:
                        results = self.check_once()
                        summary = self.last_check_summary
                    logger.info(
                        "Check cycle: attempted=%d succeeded=%d failed=%d",
                        summary.attempted,
                        summary.succeeded,
                        summary.failed,
                    )
                    if summary.failed:
                        logger.warning(
                            "Check cycle failed for UPCs: %s",
                            ", ".join(summary.failed_upcs),
                        )
                    if results:
                        logger.info("Found %d items with changes", len(results))
                    elif not summary.failed and not summary.interrupted:
                        logger.info("No changes detected in this cycle")
                except TrackerStoppedError:
                    break
                except Exception:
                    logger.exception("Unexpected error in polling cycle")
                if summary is not None and summary.interrupted:
                    logger.warning(
                        "Check cycle interrupted: attempted=%d succeeded=%d failed=%d",
                        summary.attempted,
                        summary.succeeded,
                        summary.failed,
                    )
                    break
                if self._stop_event.is_set():
                    break
                if self._stop_event.wait(interval_seconds):
                    break
        except KeyboardInterrupt:
            logger.info("Tracker stopped by user")
        finally:
            self.close()

    def stop(self) -> None:
        """Request prompt polling-loop shutdown without waiting for active work."""
        self._stop_event.set()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Tracker is closed")

    def _ensure_running(self) -> None:
        if self._stop_event.is_set():
            raise TrackerStoppedError("Tracker is stopping")

    def close(self) -> None:
        """Stop and drain operations, then close sessions/browser exactly once."""
        self.stop()
        with self.operation_lock:
            if self._closed:
                return
            self._closed = True
            with self._sessions_lock:
                for client, _ in self._sessions.values():
                    try:
                        client.close()
                    except Exception:
                        pass
                self._sessions.clear()

            def _close_browser() -> None:
                if self._browser is not None:
                    try:
                        self._browser_cm.__exit__(None, None, None)
                    except Exception:
                        logger.debug("Error closing browser", exc_info=True)
                    self._browser = None
                    self._browser_cm = None

            try:
                self._browser_executor.submit(_close_browser).result(timeout=10)
            except RuntimeError:
                pass
            except Exception:
                logger.debug("Browser cleanup timed out or failed")
            self._browser_executor.shutdown(wait=True)

    def _cleanup(self) -> None:
        self.close()

    # Backwards compat
    def _cleanup_browser(self) -> None:
        """Close the shared browser if one was opened."""
        self.close()


def _is_auth_error(exc: Exception) -> bool:
    """Check if an exception is likely caused by expired Akamai cookies."""
    msg = str(exc).lower()
    return any(s in msg for s in ("403", "429", "forbidden", "access denied", "timeout"))
