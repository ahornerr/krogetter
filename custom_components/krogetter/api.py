"""HTTP client for the Krogetter API server."""
from __future__ import annotations
import aiohttp
import logging

_LOGGER = logging.getLogger(__name__)

class KrogetterAPIError(Exception):
    """Base exception for API errors."""

class KrogetterAPI:
    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")

    async def health_check(self) -> bool:
        """Return True if server is reachable."""
        try:
            async with self._session.get(f"{self._base_url}/api/health", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
        except (aiohttp.ClientError, TimeoutError):
            return False

    async def get_items(self) -> list[dict]:
        """Get all tracked items with latest snapshots."""
        async with self._session.get(f"{self._base_url}/api/items") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def add_item(self, url: str, label: str | None = None, zip_code: str | None = None, delivery: bool = False, store_id: str | None = None) -> dict:
        """Add a tracked item."""
        payload: dict[str, str | bool] = {"url": url}
        if label:
            payload["label"] = label
        if zip_code:
            payload["zip_code"] = zip_code
        if delivery:
            payload["delivery"] = delivery
        if store_id:
            payload["store_id"] = store_id
        async with self._session.post(f"{self._base_url}/api/items", json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def remove_item(self, upc: str) -> None:
        """Remove a tracked item."""
        async with self._session.delete(f"{self._base_url}/api/items/{upc}") as resp:
            resp.raise_for_status()

    async def check_item(self, upc: str) -> dict:
        """Trigger a check for a single item."""
        async with self._session.post(f"{self._base_url}/api/items/{upc}/check") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def check_all(self) -> list[dict]:
        """Trigger a check for all items."""
        async with self._session.post(f"{self._base_url}/api/check") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_history(self, upc: str, limit: int = 100) -> list[dict]:
        """Get price history for an item."""
        async with self._session.get(f"{self._base_url}/api/items/{upc}/history", params={"limit": limit}) as resp:
            resp.raise_for_status()
            return await resp.json()
