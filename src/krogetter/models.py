"""Data models for tracked items, prices, and store information."""

import re
from dataclasses import dataclass


@dataclass
class StoreLocation:
    location_id: str          # e.g. "62000115"
    name: str                 # store name
    chain: str                # e.g. "KINGSOOPERS"
    address: str              # formatted address
    zip_code: str             # e.g. "80202"
    latitude: float | None
    longitude: float | None


# Regex patterns for parsing offer descriptions
_BUY_N_GET_M_FREE = re.compile(r"Buy (\d+) Get (\d+) Free", re.IGNORECASE)
_BUY_N_GET_M_PCT_OFF = re.compile(r"Buy (\d+) Get (\d+) (\d+)%\s*Off", re.IGNORECASE)


@dataclass
class PriceSnapshot:
    regular: float            # regular price, e.g. 11.99
    promo: float              # sale price; 0.0 if not on sale (NOT null — API returns 0)
    promo_description: str | None  # e.g. "Buy 2 Get 1 Free"; None if unavailable
    checked_at: str           # ISO timestamp of when this price was checked
    offer_template: str | None = None           # e.g. "MUST_BUY"
    offer_start: str | None = None              # ISO date string
    offer_end: str | None = None                # ISO date string
    fulfillment_price_string: str | None = None  # e.g. "Buy 2 Get 1 Free" from fulfillmentSummaries
    available: bool = True                      # False if product has no pricing at selected store
    inventory_level: str | None = None          # e.g. "HIGH", "MEDIUM", "LOW"; None if unavailable

    @property
    def is_on_sale(self) -> bool:
        """True if there's a price discount OR an active offer."""
        if not self.available:
            return False
        return self.has_offer or (self.promo > 0 and self.promo < self.regular)

    @property
    def has_offer(self) -> bool:
        """True if there is an offer description or fulfillment promo text."""
        return (
            self.promo_description is not None
            or self.fulfillment_price_string is not None
            or self.offer_template is not None
        )

    @property
    def effective_unit_price(self) -> float | None:
        """The effective per-unit price after applying any offer.

        For "Buy 2 Get 1 Free" at $11.99: you pay $23.98 for 3 units = $7.99/unit.
        For a straight discount ($8.99 from $11.99): returns $8.99.
        For no offer: returns None (use `regular` or `current_price`).
        """
        if self.promo > 0 and self.promo < self.regular:
            return self.promo

        desc = self.promo_description or self.fulfillment_price_string
        if not desc or self.regular <= 0:
            return None

        # Buy N Get M Free: pay for N, get M free → effective = regular * N / (N+M)
        m = _BUY_N_GET_M_FREE.match(desc)
        if m:
            buy_qty = int(m.group(1))
            free_qty = int(m.group(2))
            total_qty = buy_qty + free_qty
            return round(self.regular * buy_qty / total_qty, 2)

        # Buy N Get M X% Off: pay for N at regular, M at (100-X)% of regular
        m = _BUY_N_GET_M_PCT_OFF.match(desc)
        if m:
            buy_qty = int(m.group(1))
            discounted_qty = int(m.group(2))
            discount_pct = int(m.group(3))
            total_cost = (
                self.regular * buy_qty
                + self.regular * discounted_qty * (100 - discount_pct) / 100
            )
            total_qty = buy_qty + discounted_qty
            return round(total_cost / total_qty, 2)

        return None

    @property
    def current_price(self) -> float:
        """The effective current price (promo if on sale, else regular)."""
        if self.promo > 0 and self.promo < self.regular:
            return self.promo
        return self.regular

    @property
    def savings(self) -> float | None:
        """Dollar savings per unit if on sale or has offer, else None."""
        eff = self.effective_unit_price
        if eff is not None and eff < self.regular:
            return round(self.regular - eff, 2)
        return None

    @property
    def savings_percent(self) -> float | None:
        """Percentage savings per unit if on sale or has offer, else None."""
        if not self.is_on_sale or self.regular <= 0:
            return None
        saved = self.savings
        if saved is None:
            return None
        return round((saved / self.regular) * 100, 1)

    @property
    def synthetic_description(self) -> str | None:
        """A human-readable promo description.

        Precedence: fulfillment_price_string > promo_description > computed text.
        Returns None when there is no offer and no price-based sale.
        """
        if self.fulfillment_price_string:
            return self.fulfillment_price_string
        if self.promo_description:
            return self.promo_description
        if not self.is_on_sale:
            return None
        savings = self.savings
        pct = self.savings_percent
        if savings is not None and pct is not None:
            return f"Save ${savings:.2f} ({pct}% off)"
        return None


@dataclass
class Product:
    product_id: str           # Kroger product ID
    upc: str                  # UPC (13-digit, zero-padded)
    description: str          # product name
    brand: str                # brand name
    size: str | None          # e.g. "12 fl oz"
    categories: list[str]     # category strings
    price: PriceSnapshot | None  # None if no location-specific pricing
    image_url: str | None


@dataclass
class TrackedItem:
    url: str                               # original URL the user provided
    upc: str                               # extracted UPC
    label: str                             # user-provided label or auto-generated
    location_id: str | None = None         # store location ID (None = IP geolocation)
    chain: str = ""                        # e.g. "KINGSOOPERS"
    zip_code: str = ""                     # ZIP code for store resolution
    modality: str = "PICKUP"               # "PICKUP" or "DELIVERY"
    added_at: str = ""                     # ISO timestamp
