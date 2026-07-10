"""Stealth Firefox product fetcher using the Kroger product v2 API.

Fetches product/price/offer data by calling the Kroger product v2 API
directly from the browser context (with Akamai cookies). No API keys,
no OAuth, no login required.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from krogetter.models import PriceSnapshot, Product
from krogetter.url import parse_product_url

logger = logging.getLogger(__name__)


def _parse_price(price_str: str) -> float:
    """Parse a price string like 'USD 11.99' or '$11.99' into a float."""
    cleaned = price_str.replace("USD", "").replace("$", "").strip()
    return float(cleaned)


def _parse_product_data(
    product_data: dict[str, Any] | None, upc: str, modality: str = "PICKUP"
) -> Product | None:
    """Parse a Product from a product data dict (from product v2 API).

    Args:
        product_data: The product dict from the API's data.products[0].
        upc: Fallback UPC if not present in the data.
        modality: The fulfillment modality ("PICKUP", "DELIVERY") used to
                  determine availability and inventory level.
    """
    if not product_data:
        return None

    # Parse item info
    item: dict[str, Any] = product_data.get("item", {})
    description: str = item.get("description", "")
    brand: str = item.get("brand", {}).get("name", "")
    categories: list[str] = [
        c.get("name", "") for c in item.get("categories", []) if c.get("name")
    ]

    # Parse images — prefer "medium" size
    image_url: str | None = None
    for img in item.get("images", []):
        if img.get("size") == "medium":
            image_url = img.get("url")
            break

    # Parse price
    price_data: dict[str, Any] = (
        product_data.get("price", {}).get("storePrices", {}).get("regular", {})
    )
    price_raw: str = price_data.get("price", "USD 0")
    if price_raw == "USD 0":
        logger.debug("No regular price found for UPC %s; using 0.0", upc)
    regular_price: float = _parse_price(price_raw)

    # Check storePrices.promo for a separate numeric promo price (e.g. a
    # numeric discount like "$8.99" instead of a "Buy N Get 1 Free" style offer).
    store_prices: dict[str, Any] = product_data.get("price", {}).get("storePrices", {})
    promo_data: dict[str, Any] = store_prices.get("promo", {})
    promo_raw: str | None = promo_data.get("price")

    # Parse offers
    offers: list[dict[str, Any]] = product_data.get("offers", [])
    offer: dict[str, Any] = offers[0] if offers else {}
    promo_description: str | None = offer.get("defaultDescription")
    offer_template: str | None = offer.get("displayTemplate")
    # offer dates — prefer direct "start"/"end", fall back to "startDate"/"endDate" objects
    offer_start: str | None = (
        offer.get("start")
        or offer.get("startDate", {}).get("value")
    )
    offer_end: str | None = (
        offer.get("end")
        or offer.get("endDate", {}).get("value")
    )

    # Determine promo price:
    # - Numeric promo in storePrices.promo → use that value
    # - Offer without numeric promo (e.g. "Buy 2 Get 1 Free") → promo == regular
    # - No offer at all → promo == 0.0 (not on sale)
    promo_price: float
    if promo_raw and promo_raw != "USD 0":
        promo_price = _parse_price(promo_raw)
    elif offer:
        promo_price = regular_price
    else:
        promo_price = 0.0

    # Parse fulfillment summaries for availability, inventory, and price string.
    # Each summary has a "type" ("PICKUP", "DELIVERY", "IN_STORE") and an
    # "availability" object with "sellable" (bool) and "inventoryLevel" (str).
    fulfillment_summaries: list[dict[str, Any]] = product_data.get(
        "fulfillmentSummaries", []
    )

    # Find the summary matching the selected modality
    modality_summary: dict[str, Any] | None = next(
        (fs for fs in fulfillment_summaries if fs.get("type") == modality),
        None,
    )

    # Parse fulfillment price string from the modality-specific summary,
    # falling back to any summary that has one
    fulfillment_price_string: str | None = None
    if modality_summary:
        ps: str | None = modality_summary.get("regular", {}).get("priceString")
        if ps:
            fulfillment_price_string = ps
    if not fulfillment_price_string:
        for fs in fulfillment_summaries:
            ps2: str | None = fs.get("regular", {}).get("priceString")
            if ps2:
                fulfillment_price_string = ps2
                break

    # Determine availability and inventory level from the modality-specific summary
    if modality_summary:
        availability: dict[str, Any] = modality_summary.get("availability", {})
        available: bool = availability.get("sellable", False)
        inventory_level: str | None = availability.get("inventoryLevel")
    else:
        # No summary for this modality — fall back to any summary existing
        available = len(fulfillment_summaries) > 0
        inventory_level = None

    snapshot = PriceSnapshot(
        regular=regular_price,
        promo=promo_price,
        promo_description=promo_description,
        checked_at=datetime.now(UTC).isoformat(),
        offer_template=offer_template,
        offer_start=offer_start,
        offer_end=offer_end,
        fulfillment_price_string=fulfillment_price_string,
        available=available,
        inventory_level=inventory_level,
    )

    return Product(
        product_id=item.get("upc", upc),
        upc=item.get("upc", upc),
        description=description,
        brand=brand,
        size=item.get("customerFacingSize"),
        categories=categories,
        price=snapshot,
        image_url=image_url,
    )


def _safe_evaluate(page: Any, script: str, arg: Any = None, retries: int = 5) -> Any:
    """Evaluate JS in page context with retries for navigation-induced context destruction.
    
    invisible_playwright runs a real browser (not native headless), so SPA
    navigations can destroy the execution context mid-evaluate. We call
    window.stop() between retries to halt all pending navigations and
    stabilize the page before trying again.
    """
    import time
    
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return page.evaluate(script, arg)
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                logger.debug("evaluate failed (attempt %d): %s — retrying", attempt + 1, exc)
                # Stop all pending navigations/timers/network activity
                # to stabilize the execution context for the next attempt
                try:
                    page.evaluate("window.stop()")
                except Exception:
                    pass
                time.sleep(2)
    raise last_exc  # type: ignore[misc]


def select_store(
    page: Any,
    zip_code: str,
    modality: str = "PICKUP",
    store_id: str | None = None,
) -> dict[str, str] | None:
    """Select a store via the Kroger modality API.

    Must be called after loading at least one page (to get Akamai cookies)
    and after warming up the session on the homepage.

    Uses page.evaluate(fetch(...)) so the requests run inside the browser's
    JS context with the correct Akamai cookies and TLS fingerprint.

    Args:
        page: A Playwright page with an active Akamai session.
        zip_code: ZIP code to search for stores.
        modality: "PICKUP" or "DELIVERY".
        store_id: Specific store location ID for PICKUP. If None, uses the
                  nearest store. Ignored for DELIVERY (server assigns FC).

    Returns:
        Dict of LAF headers for the product API, or None on failure.
    """
    # Step 1: Search for stores by ZIP
    stores_result = _safe_evaluate(
        page,
        """async (zip) => {
        const resp = await fetch('/atlas/v1/modality/options', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/plain, */*',
                'X-Kroger-Channel': 'WEB',
            },
            body: JSON.stringify({address: {postalCode: zip}}),
        });
        return {status: resp.status, body: await resp.text()};
    }""",
        zip_code,
    )

    if stores_result["status"] != 200:
        logger.warning(
            "Store search failed for ZIP %s: HTTP %s",
            zip_code,
            stores_result["status"],
        )
        return None

    mo_data = (
        json.loads(stores_result["body"])
        .get("data", {})
        .get("modalityOptions", {})
    )
    stores = mo_data.get("storeDetails", [])
    if not stores:
        logger.warning("No stores found for ZIP %s", zip_code)
        return None

    # Step 2: Pick the modality object
    if modality == "DELIVERY":
        modality_obj = mo_data.get("DELIVERY", {})
        if not modality_obj:
            logger.warning("No DELIVERY modality available for ZIP %s", zip_code)
            return None
        # For delivery, use the first store for display purposes
        selected_store = stores[0]
    else:  # PICKUP
        pickup_mods = mo_data.get("PICKUP", [])
        if not pickup_mods:
            logger.warning("No PICKUP modality available for ZIP %s", zip_code)
            return None
        if store_id:
            # Find the matching store
            modality_obj = next(
                (
                    m
                    for m in pickup_mods
                    if m.get("destination", {}).get("locationId") == store_id
                ),
                None,
            )
            selected_store = next(
                (s for s in stores if s.get("locationId") == store_id), None
            )
            if not modality_obj or not selected_store:
                logger.warning(
                    "Store %s not found near ZIP %s; using nearest", store_id, zip_code
                )
                modality_obj = pickup_mods[0]
                selected_store = stores[0]
        else:
            modality_obj = pickup_mods[0]
            selected_store = stores[0]

    logger.info(
        "Selected store %s (%s) with modality %s",
        selected_store.get("locationId"),
        selected_store.get("vanityName"),
        modality,
    )

    # Step 3: PUT to set the modality preference
    put_body = {
        "capabilities": {
            "DELIVERY": True,
            "IN_STORE": True,
            "PICKUP": True,
            "SHIP": True,
        },
        "storeDetails": [selected_store],
        "hasIncompleteModalities": False,
        "modifiedLAFSources": False,
        "lafObject": [
            {
                "modality": {
                    "type": modality,
                    "handoffLocation": {
                        "storeId": selected_store.get("locationId", ""),
                        "facilityId": selected_store.get("storeNumber", ""),
                    },
                    "handoffAddress": {
                        "address": selected_store.get("address", {}).get("address", {}),
                        "location": selected_store.get("location", {}),
                    },
                },
                "sources": [
                    {
                        "storeId": selected_store.get("locationId", ""),
                        "facilityId": selected_store.get("storeNumber", ""),
                    }
                ],
                "listingKeys": [selected_store.get("locationId", "")],
            }
        ],
        "modalities": [modality_obj],
        "primaryModality": {modality: modality_obj.get("id")},
        "activeModality": modality,
    }

    put_result = _safe_evaluate(
        page,
        """async (body) => {
        const resp = await fetch('/atlas/v1/modality/preferences?filter.restrictLafToFc=false', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/plain, */*',
                'X-Kroger-Channel': 'WEB',
            },
            body: JSON.stringify(body),
        });
        return {status: resp.status, body: await resp.text()};
    }""",
        put_body,
    )

    if put_result["status"] != 200:
        logger.warning(
            "Store selection PUT failed: HTTP %s — %s",
            put_result["status"],
            put_result["body"][:200],
        )
        return None

    logger.info("Store selection successful")

    # Build LAF headers for subsequent product API calls.
    # The product v2 API requires these headers to return store-specific pricing.
    location_id = selected_store.get("locationId", "")
    laf_headers: dict[str, str] = {
        "x-laf-object": json.dumps(put_body["lafObject"]),
        "x-modality": json.dumps({"type": modality, "locationId": location_id}),
        "x-modality-type": modality,
        "x-facility-id": location_id,
    }
    return laf_headers


def _fetch_product_data_api(
    page: Any, upc: str, laf_headers: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Fetch product data via the Kroger product v2 API from the browser context.

    Calls the same API the SPA calls after page load to get
    product/price/offer data. Runs inside the browser context
    (with Akamai cookies and TLS fingerprint).

    Args:
        page: A Playwright page with an active Akamai session.
        upc: The 13-digit GTIN/UPC to look up.
        laf_headers: Optional LAF headers from select_store() for store-specific
                     pricing. If None, the API may return IP-geolocated pricing.

    Returns the product dict (data.products[0]) or None on failure.
    """
    # Build headers — LAF headers are required for store-specific pricing
    headers_js = "{'Accept': 'application/json, text/plain, */*', 'X-Kroger-Channel': 'WEB'"
    if laf_headers:
        for k, v in laf_headers.items():
            # Escape single quotes in JSON values for JS string
            escaped = v.replace("'", "\\'")
            headers_js += f", '{k}': '{escaped}'"
    headers_js += "}"

    result = _safe_evaluate(
        page,
        f"""async (upc) => {{
        const resp = await fetch(
            '/atlas/v1/product/v2/products?filter.gtin13s=' + upc
            + '&filter.verified=true'
            + '&projections=items.full,offers.compact,nutrition.label,'
            + 'inventory.projected,variantGroupings.compact',
            {{
                headers: {headers_js},
            }}
        );
        return {{status: resp.status, body: await resp.text()}};
    }}""",
        upc,
    )

    if result["status"] != 200:
        logger.warning(
            "Product API returned HTTP %s for UPC %s", result["status"], upc
        )
        return None

    try:
        data = json.loads(result["body"])
    except json.JSONDecodeError as exc:
        logger.warning("Product API response not valid JSON: %s", exc)
        return None

    products: list[dict[str, Any]] = data.get("data", {}).get("products", [])
    if not products:
        logger.warning("Product API returned no products for UPC %s", upc)
        return None

    return products[0]


def prepare_session(
    browser: Any,
    homepage_url: str,
    zip_code: str = "",
    modality: str = "PICKUP",
    store_id: str | None = None,
) -> tuple[Any, dict[str, str] | None, Any]:
    """Set up a browser session for product API calls.

    Flow: homepage warmup (Akamai) → robots.txt (stabilize context) → store selection.
    No product page load is needed — the product v2 API is called directly
    from the browser context after session setup.

    Args:
        browser: A browser instance (from InvisiblePlaywright).
        homepage_url: The store homepage URL (e.g. https://www.kingsoopers.com/).
        zip_code: If provided, selects a store for store-specific pricing.
        modality: "PICKUP" or "DELIVERY". Used with zip_code.
        store_id: Specific store location ID for PICKUP.

    Returns:
        (page, laf_headers, context) — caller must close page and context.
        laf_headers is None if store selection was skipped or failed.
    """
    from urllib.parse import urlparse

    parsed = urlparse(homepage_url)
    static_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    context = browser.new_context()
    page = context.new_page()

    # Homepage warmup — Akamai requires this before any API calls
    page.goto(homepage_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # Navigate to robots.txt to kill the SPA and stabilize the execution
    # context for API calls. The SPA's client-side router destroys JS
    # execution contexts mid-evaluate; robots.txt has no SPA.
    page.goto(static_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(500)

    # Store selection (if zip_code provided)
    laf_headers: dict[str, str] | None = None
    if zip_code:
        try:
            laf_headers = select_store(page, zip_code, modality, store_id)
        except Exception:
            logger.warning(
                "Store selection error; using default store pricing",
                exc_info=True,
            )
        if not laf_headers:
            logger.warning(
                "Store selection failed; using default store pricing"
            )

    return page, laf_headers, context


def fetch_product_data(
    page: Any,
    upc: str,
    laf_headers: dict[str, str] | None = None,
    modality: str = "PICKUP",
) -> Product | None:
    """Fetch and parse a single product via the product v2 API.

    Requires a prepared session from prepare_session(). This function only
    makes the API call and parses the response — no page navigation.

    Args:
        page: A Playwright page from prepare_session().
        upc: The 13-digit GTIN/UPC to look up.
        laf_headers: LAF headers from select_store() for store-specific pricing.
        modality: The fulfillment modality for availability/inventory parsing.

    Returns:
        Product with price and offer data, or None on failure.
    """
    product_data = _fetch_product_data_api(page, upc, laf_headers)
    if product_data is None:
        logger.warning("Failed to fetch product data via API for UPC %s", upc)
        return None

    product = _parse_product_data(product_data, upc, modality)
    if product is None:
        logger.warning(
            "Failed to parse product data from API response for UPC %s", upc
        )
    return product


def fetch_product(
    url_or_upc: str,
    browser: Any = None,
    zip_code: str = "",
    modality: str = "PICKUP",
    store_id: str | None = None,
) -> Product | None:
    """Fetch a single product by URL or UPC using stealth Firefox.

    Manages its own browser session. For batch fetching multiple products,
    use prepare_session() + fetch_product_data() instead to avoid repeating
    homepage warmup and store selection per product.

    Args:
        url_or_upc: A Kroger product URL or bare UPC.
        browser: An existing browser instance (for reuse in polling loops).
                 If None, a new browser is launched and closed.
        zip_code: If provided, selects a store via the Kroger modality API before
                  fetching the product. Requires warming up the Akamai session.
        modality: "PICKUP" or "DELIVERY". Used with zip_code.
        store_id: Specific store location ID for PICKUP. Requires zip_code.

    Returns:
        Product with price and offer data, or None on failure.
    """
    from invisible_playwright import InvisiblePlaywright

    # Determine the URL and UPC
    if url_or_upc.startswith(("http://", "https://")):
        url: str = url_or_upc
        try:
            upc: str = parse_product_url(url_or_upc)
        except ValueError as exc:
            logger.warning("Could not parse product URL %r: %s", url_or_upc, exc)
            return None
    else:
        upc = url_or_upc
        if not (upc.isdigit() and len(upc) == 13):
            logger.warning("Bare UPC must be a 13-digit number, got: %r", upc)
            return None
        logger.warning(
            "Bare UPC %r not yet supported for web fetcher; please provide a full URL",
            upc,
        )
        return None

    from urllib.parse import urlparse
    parsed = urlparse(url)
    homepage_url = f"{parsed.scheme}://{parsed.netloc}/"

    own_browser: bool = browser is None
    _cm: Any = None

    try:
        if own_browser:
            _cm = InvisiblePlaywright(headless=True)
            browser = _cm.__enter__()

        page, laf_headers, context = prepare_session(
            browser, homepage_url, zip_code, modality, store_id
        )
        try:
            return fetch_product_data(page, upc, laf_headers, modality)
        finally:
            page.close()
            context.close()
    except Exception:
        logger.exception("Error fetching product for %r", url_or_upc)
        return None
    finally:
        if own_browser and _cm is not None:
            try:
                _cm.__exit__(None, None, None)
            except Exception:
                logger.debug("Error closing browser", exc_info=True)
