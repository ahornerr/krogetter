"""URL parsing and UPC extraction utilities for Kroger family product URLs."""

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

KROGER_BRAND_DOMAINS = frozenset({
    "kingsoopers.com", "kroger.com", "fredmeyer.com", "ralphs.com",
    "smithsfoodanddrug.com", "harristeeter.com", "frysfood.com", "qfc.com",
    "dillons.com", "bakersplus.com", "citymarket.com", "food4less.com",
    "foodsco.net", "gerbes.com", "jaycfoods.com", "marianos.com",
    "metromarket.net", "pay-less.com", "picknsave.com",
})

# Matches 13-digit ID as the last /-delimited path segment
_UPC_PATTERN = re.compile(r"/(\d{13})$")


def parse_product_url(url: str) -> str:
    """Extract the product ID from a Kroger family product URL.

    Args:
        url: A URL like https://www.kingsoopers.com/p/coca-cola-.../0004900004825

    Returns:
        The 13-digit product ID string (used as UPC/productId in the API).

    Raises:
        ValueError: If the URL is not a valid Kroger family product URL or
                    has no 13-digit numeric ID in the path.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme: {parsed.scheme}")

    hostname = parsed.hostname or ""
    if hostname.startswith("www."):
        hostname = hostname[4:]

    if hostname not in KROGER_BRAND_DOMAINS:
        raise ValueError(f"Not a recognized Kroger family domain: {hostname}")

    path = parsed.path.rstrip("/")
    match = _UPC_PATTERN.search(path)
    if not match:
        raise ValueError(f"No 13-digit product ID found in URL path: {path}")

    upc = match.group(1)

    # Soft check-digit validation: warn but don't reject.
    # Kroger's URL IDs do not always conform to standard UPC-A check digits,
    # so strict validation would reject valid product URLs.
    if not validate_upc(upc):
        logger.debug(
            "Product ID %s does not pass UPC-A check-digit validation — "
            "this is common for Kroger URLs; proceeding anyway",
            upc,
        )

    return upc


def validate_upc(upc: str) -> bool:
    """Validate a UPC-A / GTIN-13 check digit.

    Kroger uses 13-digit zero-padded IDs in product URLs. These resemble
    GTIN-13/UPC-A codes but do not always conform to the standard check-digit
    algorithm. This function is a utility for soft validation only.

    Algorithm (UPC-A, applied to the 12-digit substring after the leading 0):
    - Sum odd positions (1st, 3rd, 5th, ... 1-indexed) × 3
    - Sum even positions (2nd, 4th, 6th, ...)
    - Check digit = (10 - (total % 10)) % 10
    - Valid if computed check digit == last digit
    """
    upc = upc.strip()
    if len(upc) != 13 or not upc.isdigit():
        return False

    upc12 = upc[1:]  # drop leading 0 → 12-digit UPC
    data = upc12[:11]
    existing_check = int(upc12[11])

    odd_sum = sum(int(ch) for i, ch in enumerate(data, start=1) if i % 2 == 1)
    even_sum = sum(int(ch) for i, ch in enumerate(data, start=1) if i % 2 == 0)
    total = (odd_sum * 3) + even_sum
    computed_check = (10 - (total % 10)) % 10

    return computed_check == existing_check


def extract_upc_or_passthrough(input_str: str) -> str:
    """Extract a product ID from a URL, or accept a bare 13-digit ID.

    If the input starts with http:// or https://, it is parsed as a Kroger
    product URL. Otherwise, it is treated as a bare 13-digit product ID.

    Raises:
        ValueError: If a URL is invalid or a bare ID is not 13 digits.
    """
    input_str = input_str.strip()

    if input_str.startswith(("http://", "https://")):
        return parse_product_url(input_str)

    # Bare ID: must be 13 digits
    if not (input_str.isdigit() and len(input_str) == 13):
        raise ValueError(
            f"Expected a 13-digit product ID or a Kroger product URL, got: {input_str!r}"
        )

    # Soft check-digit validation (same as parse_product_url)
    if not validate_upc(input_str):
        logger.debug(
            "Product ID %s does not pass UPC-A check-digit validation — "
            "this is common for Kroger URLs; proceeding anyway",
            input_str,
        )

    return input_str
