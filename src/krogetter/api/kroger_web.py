"""Stealth Firefox product fetcher using __INITIAL_STATE__ extraction.

This is the primary data source — fetches the product page HTML via invisible_playwright
(stealth Firefox), extracts the embedded __INITIAL_STATE__ JSON, and parses
product/price/offer data. No API keys required.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from krogetter.models import PriceSnapshot, Product
from krogetter.url import parse_product_url

logger = logging.getLogger(__name__)


def _extract_initial_state(page: Any) -> dict[str, Any] | None:
    """Extract __INITIAL_STATE__ from a loaded page via eval() in page context.

    The __INITIAL_STATE__ is set by a <script> tag that assigns it via eval()
    (e.g. ``eval(function(p,a,c,k,e,d){...})``). It is NOT a simple
    ``window.__INITIAL_STATE__ = {…}`` literal — the script must be evaluated
    in the page context for the variable to become accessible on window.
    Using page.evaluate() is the only reliable way to trigger this
    deferred assignment.
    """
    result = page.evaluate('''() => {
        // First check if __INITIAL_STATE__ is already on window
        // (may be set by an earlier script that already executed)
        if (typeof window.__INITIAL_STATE__ !== 'undefined') {
            try {
                return JSON.stringify(window.__INITIAL_STATE__);
            } catch(e) {}
        }

        // Try each script that mentions __INITIAL_STATE__
        const scripts = document.querySelectorAll('script');
        let matchCount = 0;
        for (const s of scripts) {
            if (s.textContent && s.textContent.includes('__INITIAL_STATE__')) {
                matchCount++;
                try {
                    eval(s.textContent);
                    if (typeof window.__INITIAL_STATE__ !== 'undefined') {
                        return JSON.stringify(window.__INITIAL_STATE__);
                    }
                } catch(e) {
                    // This script failed; try the next one
                }
            }
        }
        return JSON.stringify({__error: 'no script successfully set __INITIAL_STATE__', scriptMatches: matchCount, totalScripts: scripts.length});
    }''')
    if not result:
        logger.debug("_extract_initial_state: page.evaluate returned empty/null")
        return None
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError as exc:
        logger.debug("_extract_initial_state: failed to parse result as JSON: %s", exc)
        return None
    if isinstance(parsed, dict) and "__error" in parsed:
        logger.debug("_extract_initial_state: %s", parsed["__error"])
        return None
    logger.debug(
        "_extract_initial_state: success, top-level keys: %s",
        list(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__,
    )
    return parsed


def _parse_price(price_str: str) -> float:
    """Parse a price string like 'USD 11.99' or '$11.99' into a float."""
    cleaned = price_str.replace("USD", "").replace("$", "").strip()
    return float(cleaned)


def _get_product_data(state: dict[str, Any], upc: str) -> dict[str, Any] | None:
    """Extract the product data dict from __INITIAL_STATE__.

    Tries calypso.domains.products.{upc}.data first, then falls back to
    calypso.useCases.getProducts.pdpSSR.response.data.products[0].
    """
    calypso: dict[str, Any] = state.get("calypso", {})
    if not calypso:
        logger.debug(
            "State has no 'calypso' key; top-level keys: %s",
            list(state.keys()),
        )
        return None

    # Primary path: calypso.domains.products.{upc}.data
    products_domain: dict[str, Any] = calypso.get("domains", {}).get("products", {})
    if products_domain:
        available_upcs = list(products_domain.keys())
        logger.debug(
            "calypso.domains.products has UPCs: %s (looking for %s)",
            available_upcs,
            upc,
        )
    product_data: dict[str, Any] = products_domain.get(upc, {}).get("data", {})
    if product_data:
        return product_data

    # Fallback: calypso.useCases.getProducts.pdpSSR.response.data.products[0]
    products: list[dict[str, Any]] = (
        calypso.get("useCases", {})
        .get("getProducts", {})
        .get("pdpSSR", {})
        .get("response", {})
        .get("data", {})
        .get("products", [])
    )
    if products:
        logger.debug(
            "Primary product path miss; using pdpSSR fallback (%d products)",
            len(products),
        )
        return products[0]

    # Log what we actually see for debugging
    logger.debug(
        "Product not found in state. calypso keys: %s, "
        "calypso.domains keys: %s, calypso.useCases keys: %s",
        list(calypso.keys()),
        list(calypso.get("domains", {}).keys()),
        list(calypso.get("useCases", {}).keys()),
    )
    return None


def _parse_product_data(product_data: dict[str, Any] | None, upc: str) -> Product | None:
    """Parse a Product from a product data dict (from API or __INITIAL_STATE__)."""
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

    # Parse fulfillment price string (promo text displayed next to price)
    fulfillment_summaries: list[dict[str, Any]] = product_data.get(
        "fulfillmentSummaries", []
    )
    fulfillment_price_string: str | None = None
    for fs in fulfillment_summaries:
        reg: dict[str, Any] = fs.get("regular", {})
        ps: str | None = reg.get("priceString")
        if ps:
            fulfillment_price_string = ps
            break

    # A product is "available" if it has fulfillment summaries with pricing.
    # When a product is not carried at the selected store, fulfillmentSummaries
    # is empty and storePrices is empty (regular price = 0.0).
    available = len(fulfillment_summaries) > 0

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

    The website moved from SSR (product data in __INITIAL_STATE__) to
    client-side fetching. The SPA calls this API after page load to get
    product/price/offer data. We call it directly from the browser context
    (with Akamai cookies) instead of relying on __INITIAL_STATE__.

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


def fetch_product(
    url_or_upc: str,
    browser: Any = None,
    zip_code: str = "",
    modality: str = "PICKUP",
    store_id: str | None = None,
) -> Product | None:
    """Fetch a product by URL or UPC using stealth Firefox.

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

    # Determine the URL to navigate to
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
        # We need a URL to navigate, but we don't know the product slug.
        # Bare UPC is not yet supported without a search step.
        logger.warning(
            "Bare UPC %r not yet supported for web fetcher; please provide a full URL",
            upc,
        )
        return None

    own_browser: bool = browser is None
    _cm: Any = None

    try:
        if own_browser:
            _cm = InvisiblePlaywright(headless=True)
            browser = _cm.__enter__()

        context = browser.new_context()
        page = context.new_page()
        try:
            # Determine the domain for homepage warmup
            from urllib.parse import urlparse
            parsed = urlparse(url)
            homepage_url = f"{parsed.scheme}://{parsed.netloc}/"

            # Warm up Akamai session on homepage first.
            # Going directly to a product page gets "Access Denied" because
            # Akamai needs to set cookies via the homepage challenge first.
            page.goto(homepage_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # Load the product page to establish the Akamai session for
            # this specific URL path. The SPA will make client-side API
            # calls, but we don't need to capture those — we'll call the
            # product API ourselves after store selection.
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)

            # Navigate to a static page on the same origin to kill the SPA
            # and stabilize the execution context for API calls.
            # The SPA's client-side router destroys JS execution contexts
            # mid-evaluate; robots.txt has no SPA so the context is stable.
            static_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            page.goto(static_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(500)

            # If a ZIP code is provided, select a store before fetching
            # product data. This sets cookies that determine which store's
            # pricing the product API returns.
            laf_headers: dict[str, str] | None = None
            if zip_code:
                try:
                    laf_headers = select_store(
                        page, zip_code, modality, store_id
                    )
                except Exception:
                    logger.warning(
                        "Store selection error; using default store pricing",
                        exc_info=True,
                    )

                if not laf_headers:
                    logger.warning(
                        "Store selection failed; using default store pricing"
                    )

            # Fetch product data via the product v2 API.
            # The website moved from SSR to client-side fetching, so
            # __INITIAL_STATE__ no longer contains product data. We call
            # the same API the SPA would call, from the browser context
            # (with Akamai cookies and store selection cookies).
            product_data = _fetch_product_data_api(page, upc, laf_headers)
            if product_data is None:
                logger.warning(
                    "Failed to fetch product data via API for %r", url
                )
                return None

            product = _parse_product_data(product_data, upc)
            if product is None:
                logger.warning(
                    "Failed to parse product data from API response for %r", url
                )
            return product
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
