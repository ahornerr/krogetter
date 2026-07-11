"""JSON/JSONL persistence for tracked items and price history."""

import dataclasses
import json
import logging
import os
from pathlib import Path

from krogetter.models import PriceSnapshot, TrackedItem, Offer

logger = logging.getLogger(__name__)


class Storage:
    """Manages tracked_items.json (read-write) and history.jsonl (append-only).

    Files are written atomically: write to a .tmp file, then os.replace.
    """

    def __init__(self, data_dir: str | Path) -> None:
        """Initialize storage with a data directory.

        Creates the directory if it doesn't exist.
        """
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._items_file = self._data_dir / "tracked_items.json"
        self._history_file = self._data_dir / "history.jsonl"

    # ------------------------------------------------------------------ #
    #  Tracked items (tracked_items.json)
    # ------------------------------------------------------------------ #

    def load_items(self) -> list[TrackedItem]:
        """Load tracked items from JSON. Returns empty list if file doesn't exist."""
        if not self._items_file.exists():
            return []

        try:
            content = self._items_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to read %s: %s", self._items_file, exc)
            return []

        try:
            raw_list: list[dict] = json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error(
                "Failed to parse %s (corrupted JSON): %s — returning empty list",
                self._items_file,
                exc,
            )
            return []

        items: list[TrackedItem] = []
        for idx, raw in enumerate(raw_list):
            try:
                items.append(TrackedItem(**raw))
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping invalid item at index %d in %s: %s",
                    idx,
                    self._items_file,
                    exc,
                )

        return items

    def save_items(self, items: list[TrackedItem]) -> None:
        """Save tracked items to JSON atomically (write .tmp, rename)."""
        # Keep a .bak copy of the previous version before overwriting
        if self._items_file.exists():
            bak_file = self._items_file.with_suffix(".json.bak")
            try:
                bak_file.write_bytes(self._items_file.read_bytes())
            except OSError as exc:
                logger.warning("Failed to create backup %s: %s", bak_file, exc)

        tmp_file = self._items_file.with_suffix(".json.tmp")
        try:
            data = [dataclasses.asdict(item) for item in items]
            payload = json.dumps(data, indent=2, ensure_ascii=False)
            tmp_file.write_text(payload, encoding="utf-8")
            os.replace(tmp_file, self._items_file)
        except OSError as exc:
            logger.error("Failed to save %s: %s", self._items_file, exc)
            # Clean up the tmp file if it still exists
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass
            raise

    def add_item(self, item: TrackedItem) -> None:
        """Add a tracked item. Raises ValueError if UPC already tracked.

        Note: Deduplication is by UPC only, not (UPC, location_id). This means
        a given product can only be tracked at one store at a time. This is
        intentional for simplicity — most users track items at their usual store.
        If multi-store tracking is needed later, this can be extended.
        """
        items = self.load_items()
        for existing in items:
            if existing.upc == item.upc:
                raise ValueError(f"UPC {item.upc!r} is already being tracked")
        items.append(item)
        self.save_items(items)

    def remove_item(self, upc: str) -> bool:
        """Remove a tracked item by UPC. Returns True if removed, False if not found."""
        items = self.load_items()
        new_items = [item for item in items if item.upc != upc]
        if len(new_items) == len(items):
            return False
        self.save_items(new_items)
        return True

    def update_item_label(self, upc: str, label: str) -> bool:
        """Update the label of a tracked item. Returns True if updated."""
        items = self.load_items()
        for item in items:
            if item.upc == upc:
                item.label = label
                self.save_items(items)
                return True
        return False

    # ------------------------------------------------------------------ #
    #  Price history (history.jsonl — append-only)
    # ------------------------------------------------------------------ #

    def append_history(self, upc: str, snapshot: PriceSnapshot) -> None:
        """Append a price snapshot to the history file (JSONL, one JSON object per line).

        Each line: {"upc": "...", "regular": ..., "promo": ...,
                     "promo_description": ..., "checked_at": "..."}
        """
        entry = {
            "upc": upc,
            "regular": snapshot.regular,
            "promo": snapshot.promo,
            "promo_description": snapshot.promo_description,
            "checked_at": snapshot.checked_at,
            "offer_template": snapshot.offer_template,
            "offer_start": snapshot.offer_start,
            "offer_end": snapshot.offer_end,
            "fulfillment_price_string": snapshot.fulfillment_price_string,
            "available": snapshot.available,
            "inventory_level": snapshot.inventory_level,
            "offers": [
                {
                    "description": o.description,
                    "template": o.template,
                    "start": o.start,
                    "end": o.end,
                }
                for o in snapshot.offers
            ],
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"

        # Append mode: not atomic, but a crash mid-write produces at most one
        # partial line, which load_history skips via corrupted-line handling.
        try:
            with open(self._history_file, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            logger.error("Failed to append history for UPC %s: %s", upc, exc)

    def load_history(self, upc: str, limit: int = 100) -> list[dict]:
        """Load recent history entries for a UPC. Returns most recent first.

        Reads the JSONL file, filters by UPC, and returns the last `limit`
        entries. Handles corrupted lines gracefully (skip + log warning).
        """
        if not self._history_file.exists():
            return []

        entries: list[dict] = []
        try:
            with open(self._history_file, encoding="utf-8") as fh:
                for line_num, line in enumerate(fh, start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.warning(
                            "Skipping corrupted line %d in %s: %s",
                            line_num,
                            self._history_file,
                            exc,
                        )
                        continue
                    if obj.get("upc") == upc:
                        entries.append(obj)
        except OSError as exc:
            logger.error("Failed to read %s: %s", self._history_file, exc)
            return []

        # Most recent first (reverse chronological)
        entries.reverse()
        return entries[:limit]
