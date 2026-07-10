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
        const scripts = document.querySelectorAll('script');
        for (const s of scripts) {
            if (s.textContent && s.textContent.includes('__INITIAL_STATE__')) {
                try {
                    eval(s.textContent);
                    if (typeof window.__INITIAL_STATE__ !== 'undefined') {
                        return JSON.stringify(window.__INITIAL_STATE__);
                    }
                } catch(e) { return null; }
            }
        }
        return null;
    }''')
    if result:
        return json.loads(result)
    return None


def _parse_price(price_str: str) -> float:
    """Parse a price string like 'USD 11.99' or '$11.99' into a float."""
    cleaned = price_str.replace("USD", "").replace("$", "").strip()
    return float(cleaned)


def _get_product_data(state: dict[str, Any], upc: str) -> dict[str, Any] | None:
    """Extract the product data dict from __INITIAL_STATE__.

    Tries calypso.domains.products.{upc}.data first, then falls back to
    calypso.useCases.getProducts.pdpSSR.response.data.products[0].
    """
    # Primary path: calypso.domains.products.{upc}.data
    product_data: dict[str, Any] | None = (
        state.get("calypso", {})
        .get("domains", {})
        .get("products", {})
        .get(upc, {})
        .get("data", {})
    )
    if product_data:
        return product_data

    # Fallback: calypso.useCases.getProducts.pdpSSR.response.data.products[0]
    products: list[dict[str, Any]] = (
        state.get("calypso", {})
        .get("useCases", {})
        .get("getProducts", {})
        .get("pdpSSR", {})
        .get("response", {})
        .get("data", {})
        .get("products", [])
    )
    if products:
        return products[0]

    return None


def _parse_product_from_state(state: dict[str, Any], upc: str) -> Product | None:
    """Parse a Product from the __INITIAL_STATE__ JSON."""
    product_data = _get_product_data(state, upc)
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
    fulfillment_price_string: str | None = None
    for fs in product_data.get("fulfillmentSummaries", []):
        reg: dict[str, Any] = fs.get("regular", {})
        ps: str | None = reg.get("priceString")
        if ps:
            fulfillment_price_string = ps
            break

    snapshot = PriceSnapshot(
        regular=regular_price,
        promo=promo_price,
        promo_description=promo_description,
        checked_at=datetime.now(UTC).isoformat(),
        offer_template=offer_template,
        offer_start=offer_start,
        offer_end=offer_end,
        fulfillment_price_string=fulfillment_price_string,
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
    navigations can destroy the execution context mid-evaluate. We retry
    after a short wait for the page to settle.
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
                time.sleep(3)
    raise last_exc  # type: ignore[misc]


def select_store(
    page: Any,
    zip_code: str,
    modality: str = "PICKUP",
    store_id: str | None = None,
) -> bool:
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
        True if the store was successfully selected, False otherwise.
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
        return False

    mo_data = (
        json.loads(stores_result["body"])
        .get("data", {})
        .get("modalityOptions", {})
    )
    stores = mo_data.get("storeDetails", [])
    if not stores:
        logger.warning("No stores found for ZIP %s", zip_code)
        return False

    # Step 2: Pick the modality object
    if modality == "DELIVERY":
        modality_obj = mo_data.get("DELIVERY", {})
        if not modality_obj:
            logger.warning("No DELIVERY modality available for ZIP %s", zip_code)
            return False
        # For delivery, use the first store for display purposes
        selected_store = stores[0]
    else:  # PICKUP
        pickup_mods = mo_data.get("PICKUP", [])
        if not pickup_mods:
            logger.warning("No PICKUP modality available for ZIP %s", zip_code)
            return False
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
        return False

    logger.info("Store selection successful")
    return True


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
            # Load the product page and extract state.
            # This is the primary data — even if store selection fails later,
            # we still have valid product/pricing data (IP-geolocated).
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function(
                "Array.from(document.querySelectorAll('script')).some(s => s.textContent && s.textContent.includes('__INITIAL_STATE__'))",
                timeout=20000,
            )
            page.wait_for_timeout(1000)

            state = _extract_initial_state(page)
            if state is None:
                logger.warning("No __INITIAL_STATE__ found for %r", url)
                return None

            # If a ZIP code is provided, try to select a store and reload
            # to get store-specific pricing. If this fails, we fall back to
            # the IP-geolocated pricing from the first load.
            if zip_code:
                store_selected = False
                try:
                    store_selected = select_store(
                        page, zip_code, modality, store_id
                    )
                except Exception:
                    logger.warning(
                        "Store selection error; using default store pricing",
                        exc_info=True,
                    )

                if store_selected:
                    # Reload product page with the selected store cookie
                    try:
                        page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                        page.wait_for_function(
                            "Array.from(document.querySelectorAll('script')).some(s => s.textContent && s.textContent.includes('__INITIAL_STATE__'))",
                            timeout=20000,
                        )
                        page.wait_for_timeout(1000)
                        new_state = _extract_initial_state(page)
                        if new_state is not None:
                            state = new_state
                    except Exception:
                        logger.warning(
                            "Product page reload failed after store selection; "
                            "using default store pricing"
                        )
                else:
                    logger.warning(
                        "Store selection failed; using default store pricing"
                    )

            product = _parse_product_from_state(state, upc)
            if product is None:
                logger.warning(
                    "Failed to parse product data from __INITIAL_STATE__ for %r", url
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
