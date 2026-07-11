"""Kroger product fetcher using the product v2 API.

Uses invisible_playwright (stealth Firefox) for Akamai warmup and all API
calls go through page.evaluate(fetch(...)) — the only mechanism that
works because Akamai blocks all Python HTTP clients (httpx, curl_cffi,
even Playwright's own page.request).
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from krogetter.models import PriceSnapshot, Product
from krogetter.url import parse_product_url

logger = logging.getLogger(__name__)


def _safe_evaluate(page: Any, script: str, arg: Any = None, retries: int = 5) -> Any:
    """Evaluate JavaScript in the page context, with retries for navigation-induced
    context destruction. Calls window.stop() between retries."""
    for attempt in range(retries):
        try:
            if arg is not None:
                return page.evaluate(script, arg)
            return page.evaluate(script)
        except Exception as exc:
            if attempt < retries - 1:
                logger.debug(
                    "_safe_evaluate attempt %d failed: %s, retrying...",
                    attempt + 1, exc,
                )
                try:
                    page.evaluate("window.stop()")
                except Exception:
                    pass
                page.wait_for_timeout(500)
            else:
                raise


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


def select_store(
    page: Any,
    zip_code: str,
    modality: str = "PICKUP",
    store_id: str | None = None,
) -> dict[str, str] | None:
    """Search for stores by ZIP and build LAF headers for the product API.

    Uses page.evaluate(fetch(...)) because Akamai blocks Python HTTP clients
    on API endpoints. Only the browser's own JavaScript fetch() is allowed.

    Args:
        page: A Playwright page (from prepare_session()).
        zip_code: ZIP code to search for stores.
        modality: "PICKUP" or "DELIVERY".
        store_id: Specific store location ID for PICKUP. If None, uses the
                  nearest store. Ignored for DELIVERY (server assigns FC).

    Returns:
        Dict with x-laf-object header, or None on failure.
    """
    try:
        result = _safe_evaluate(
            page,
            """async (zip) => {
            const resp = await fetch('/atlas/v1/modality/options', {
                method: 'POST',
                headers: {'Content-Type': 'application/json',
                          'Accept': 'application/json, text/plain, */*',
                          'X-Kroger-Channel': 'WEB'},
                body: JSON.stringify({address: {postalCode: zip}}),
            });
            return {status: resp.status, body: await resp.text()};
        }""",
            zip_code,
        )
    except Exception:
        logger.warning("Store search evaluate failed for ZIP %s", zip_code, exc_info=True)
        return None

    if result.get("status") != 200:
        logger.warning(
            "Store search failed for ZIP %s: HTTP %s", zip_code, result.get("status")
        )
        return None

    try:
        body = json.loads(result["body"])
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Store search response not valid JSON: %s", exc)
        return None

    mo_data = body.get("data", {}).get("modalityOptions", {})
    stores = mo_data.get("storeDetails", [])
    if not stores:
        logger.warning("No stores found for ZIP %s", zip_code)
        return None

    # Pick the store
    if modality == "DELIVERY":
        if not mo_data.get("DELIVERY"):
            logger.warning("No DELIVERY modality available for ZIP %s", zip_code)
            return None
        selected_store = stores[0]
    else:  # PICKUP
        pickup_mods = mo_data.get("PICKUP", [])
        if not pickup_mods:
            logger.warning("No PICKUP modality available for ZIP %s", zip_code)
            return None
        if store_id:
            selected_store = next(
                (s for s in stores if s.get("locationId") == store_id), None
            )
            if not selected_store:
                logger.warning(
                    "Store %s not found near ZIP %s; using nearest",
                    store_id,
                    zip_code,
                )
                selected_store = stores[0]
        else:
            selected_store = stores[0]

    logger.info(
        "Selected store %s (%s) with modality %s",
        selected_store.get("locationId"),
        selected_store.get("vanityName"),
        modality,
    )

    # Build LAF header — only x-laf-object is required by the product API
    location_id = selected_store.get("locationId", "")
    laf_object = [
        {
            "modality": {
                "type": modality,
                "handoffLocation": {
                    "storeId": location_id,
                    "facilityId": selected_store.get("storeNumber", ""),
                },
                "handoffAddress": {
                    "address": selected_store.get("address", {}).get("address", {}),
                    "location": selected_store.get("location", {}),
                },
            },
            "sources": [
                {
                    "storeId": location_id,
                    "facilityId": selected_store.get("storeNumber", ""),
                }
            ],
            "listingKeys": [location_id],
        }
    ]
    return {"x-laf-object": json.dumps(laf_object)}


def _get_default_store_laf(page: Any) -> dict[str, str] | None:
    """Get LAF headers for the IP-geolocated default store.

    When no zip code is provided, the homepage warmup sets a default
    modality cookie based on IP geolocation. This calls the modality
    preferences endpoint to retrieve the full LAF object for that store.
    """
    try:
        result = _safe_evaluate(
            page,
            """async () => {
            const resp = await fetch(
                '/atlas/v1/modality/preferences?filter.restrictLafToFc=false',
                { method: 'POST',
                  headers: {'Accept': 'application/json, text/plain, */*',
                            'X-Kroger-Channel': 'WEB'} }
            );
            return {status: resp.status, body: await resp.text()};
        }""",
        )
    except Exception:
        logger.warning("Default store lookup evaluate failed", exc_info=True)
        return None

    if result.get("status") != 200:
        logger.warning(
            "Default store lookup failed: HTTP %s", result.get("status")
        )
        return None

    try:
        body = json.loads(result["body"])
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Default store response not valid JSON: %s", exc)
        return None

    laf = body.get("data", {}).get("modalityPreferences", {}).get("lafObject", [])
    if not laf:
        logger.warning("No LAF object in default store preferences")
        return None

    logger.info("Using IP-geolocated default store")
    return {"x-laf-object": json.dumps(laf)}


def _fetch_product_data_api(
    page: Any, upc: str, laf_headers: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Fetch product data via the Kroger product v2 API using page.evaluate(fetch()).

    Args:
        page: A Playwright page from prepare_session().
        upc: The 13-digit GTIN/UPC to look up.
        laf_headers: LAF headers from select_store() for store-specific pricing.

    Returns the product dict (data.products[0]) or None on failure.
    """
    # Build headers as a JS object literal, escaping single quotes in values
    headers_js = "{'Accept': 'application/json, text/plain, */*', 'X-Kroger-Channel': 'WEB'"
    if laf_headers:
        for k, v in laf_headers.items():
            escaped = v.replace("'", "\\'")
            headers_js += f", '{k}': '{escaped}'"
    headers_js += "}"

    script = f"""async (upc) => {{
    const resp = await fetch(
        '/atlas/v1/product/v2/products?filter.gtin13s=' + upc
        + '&filter.verified=true'
        + '&projections=items.full,offers.compact,nutrition.label,'
        + 'inventory.projected,variantGroupings.compact',
        {{ headers: {headers_js} }}
    );
    return {{status: resp.status, body: await resp.text()}};
}}"""

    try:
        result = _safe_evaluate(page, script, upc)
    except Exception:
        logger.warning("Product API evaluate failed for UPC %s", upc, exc_info=True)
        return None

    if result.get("status") != 200:
        logger.warning("Product API returned HTTP %s for UPC %s", result.get("status"), upc)
        return None

    try:
        data = json.loads(result["body"])
    except (json.JSONDecodeError, TypeError) as exc:
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
    """Warm up Akamai in the browser, then return page+context for API calls.

    Navigates to the homepage (Akamai challenge), then to robots.txt to
    stabilize the execution context for page.evaluate(fetch()). The page
    stays open — caller is responsible for closing page and context.

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
    parsed = urlparse(homepage_url)
    static_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    context = browser.new_context()
    page = context.new_page()

    # Browser warmup — Akamai challenge
    page.goto(homepage_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # Navigate to robots.txt to stabilize the execution context
    # for page.evaluate(fetch(...)). This prevents context destruction
    # during API calls.
    page.goto(static_url, wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(500)

    # Store selection — zip_code selects a specific store; without it,
    # fall back to the IP-geolocated default store from modality preferences.
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
            logger.warning("Store selection failed; using default store pricing")

    if not laf_headers:
        laf_headers = _get_default_store_laf(page)

    return page, laf_headers, context


def fetch_product_data(
    page: Any,
    upc: str,
    laf_headers: dict[str, str] | None = None,
    modality: str = "PICKUP",
) -> Product | None:
    """Fetch and parse a single product via the product v2 API.

    Requires a prepared page from prepare_session().

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
