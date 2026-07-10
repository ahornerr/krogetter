"""Command-line interface for krogetter."""

import sys
from datetime import datetime, timezone
from typing import NoReturn

import click

from krogetter.config import Config
from krogetter.logging_setup import setup_logging
from krogetter.models import TrackedItem
from krogetter.storage import Storage
from krogetter.tracker import Tracker, _snapshot_from_history
from krogetter.url import extract_upc_or_passthrough, slug_to_label


def _error(msg: str) -> NoReturn:
    """Print an error message and exit with code 1."""
    click.echo(f"Error: {msg}", err=True)
    sys.exit(1)


def _get_config(ctx: click.Context) -> Config:
    """Load Config from env.

    Config is loaded lazily so that --help works without any setup.
    """
    try:
        config = Config.from_env()
    except ValueError as exc:
        _error(str(exc))

    # Apply config-based log level if not already set via CLI option
    log_level: str | None = ctx.obj.get("_log_level")  # type: ignore[union-attr]
    if log_level is None:
        setup_logging(config.log_level)

    return config


@click.group()
@click.option(
    "--log-level", default=None, help="Logging level (DEBUG, INFO, WARNING, ERROR)"
)
@click.pass_context
def main(ctx: click.Context, log_level: str | None) -> None:
    """Krogetter: Track sale prices on Kroger family store websites."""
    ctx.ensure_object(dict)
    # Store log_level for later use; config is loaded lazily when a
    # command actually runs (so --help works without any setup).
    ctx.obj["_log_level"] = log_level

    # Set up basic logging first (before config is loaded).
    # Config-based log level will be applied in _get_config if needed.
    if log_level is not None:
        setup_logging(log_level)


@main.command()
@click.argument("url_or_upc")
@click.option("--label", "-l", default=None, help="Custom label for this item")
@click.option("--store", "store_id", default=None, help="Kroger store location ID (e.g. 62000093). Requires --zip.")
@click.option("--zip", "zip_code", default=None, help="ZIP code for store selection. Selects nearest store for pickup.")
@click.option("--delivery", is_flag=True, help="Use delivery modality instead of pickup. Requires --zip.")
@click.pass_context
def add(
    ctx: click.Context,
    url_or_upc: str,
    label: str | None,
    store_id: str | None,
    zip_code: str | None,
    delivery: bool,
) -> None:
    """Add a product to track by URL or UPC.

    Examples:

        krogetter add https://www.kingsoopers.com/p/coca-cola-.../0004900004825

        krogetter add 0004900004825 --label "Coke Vanilla"

        krogetter add https://www.kingsoopers.com/p/.../0004900004825 --zip 80207

        krogetter add https://www.kingsoopers.com/p/.../0004900004825 --zip 80207 --delivery

        krogetter add https://www.kingsoopers.com/p/.../0004900004825 --zip 80207 --store 62000093

    By default, uses IP-based geolocation for store pricing.
    Use --zip to select a store near a specific ZIP code.
    Use --delivery with --zip to check delivery pricing instead of pickup.
    Use --store with --zip to select a specific store for pickup.
    """
    config = _get_config(ctx)

    # Parse URL or UPC
    try:
        upc = extract_upc_or_passthrough(url_or_upc)
    except ValueError as exc:
        _error(str(exc))

    # Validate options
    if store_id and not zip_code:
        _error("--store requires --zip to search for the store")
    if delivery and not zip_code:
        _error("--delivery requires --zip")

    modality = "DELIVERY" if delivery else "PICKUP"

    # Auto-derive label from URL slug if not provided
    if label is None and url_or_upc.startswith(("http://", "https://")):
        label = slug_to_label(url_or_upc)

    # Create TrackedItem
    item = TrackedItem(
        url=url_or_upc,
        upc=upc,
        label=label or upc,
        location_id=store_id,
        zip_code=zip_code or "",
        modality=modality,
        added_at=datetime.now(timezone.utc).isoformat(),
    )

    # Add to storage
    storage = Storage(config.data_dir)
    try:
        storage.add_item(item)
    except ValueError as exc:
        _error(str(exc))

    click.echo(f"Added: {item.label} (UPC: {item.upc})")
    if zip_code:
        if store_id:
            click.echo(f"  Store: {store_id} (PICKUP, ZIP {zip_code})")
        elif delivery:
            click.echo(f"  Delivery to ZIP {zip_code}")
        else:
            click.echo(f"  Pickup near ZIP {zip_code} (nearest store)")
    else:
        click.echo(f"  Store: auto-detected via IP geolocation")


@main.command(name="list")
@click.pass_context
def list_items(ctx: click.Context) -> None:
    """List all tracked items with their last known price."""
    config = _get_config(ctx)
    storage = Storage(config.data_dir)
    items = storage.load_items()

    if not items:
        click.echo("No tracked items.")
        return

    # Header
    click.echo(
        f"{'UPC':<16} {'Label':<28} {'Store':<12} {'Price':<10} {'Eff/Unit':<10} "
        f"{'On Sale':<8} {'Offer':<20} {'Checked':<20}"
    )
    click.echo("-" * 126)

    for item in items:
        history = storage.load_history(item.upc, limit=1)
        if history:
            last = history[0]
            try:
                snap = _snapshot_from_history(last)
            except Exception:
                snap = None

            if snap is not None:
                price = f"${snap.regular:.2f}"
                eff = snap.effective_unit_price
                eff_str = f"${eff:.2f}" if eff is not None else "-"
                on_sale = "Yes" if snap.is_on_sale else "No"

                # Build offer info
                offer_parts: list[str] = []
                offer_template = last.get("offer_template")
                promo_desc = last.get("promo_description")
                fulfillment = last.get("fulfillment_price_string")
                if fulfillment:
                    offer_parts.append(fulfillment[:18])
                elif promo_desc:
                    offer_parts.append(promo_desc[:18])
                elif offer_template:
                    offer_parts.append(offer_template[:18])
                offer = " ".join(offer_parts) if offer_parts else "-"
                checked = last["checked_at"]
            else:
                price = "N/A"
                eff_str = "N/A"
                on_sale = "N/A"
                offer = "N/A"
                checked = "Never"
        else:
            price = "N/A"
            eff_str = "N/A"
            on_sale = "N/A"
            offer = "N/A"
            checked = "Never"

        label_display = (item.label or item.upc)[:26]
        upc_display = item.upc[:14]
        if item.zip_code:
            if item.modality == "DELIVERY":
                store_display = f"DEL {item.zip_code}"[:10]
            elif item.location_id:
                store_display = f"{item.location_id}"[:10]
            else:
                store_display = f"PICK {item.zip_code}"[:10]
        else:
            store_display = "auto"[:10]

        click.echo(
            f"{upc_display:<16} {label_display:<28} {store_display:<12} "
            f"{price:<10} {eff_str:<10} {on_sale:<8} {offer:<20} {checked:<20}"
        )


@main.command()
@click.argument("url_or_upc")
@click.pass_context
def remove(ctx: click.Context, url_or_upc: str) -> None:
    """Remove a tracked item by URL or UPC."""
    config = _get_config(ctx)

    try:
        upc = extract_upc_or_passthrough(url_or_upc)
    except ValueError as exc:
        _error(str(exc))

    storage = Storage(config.data_dir)
    removed = storage.remove_item(upc)
    if removed:
        click.echo(f"Removed item with UPC: {upc}")
    else:
        click.echo(f"No tracked item found with UPC: {upc}")


@main.command()
@click.argument("url_or_upc", required=False)
@click.pass_context
def check(ctx: click.Context, url_or_upc: str | None) -> None:
    """Check tracked items for price changes (one-shot, no loop).

    If a URL/UPC is given, check only that item. Otherwise check all.
    """
    config = _get_config(ctx)
    storage = Storage(config.data_dir)
    tracker = Tracker(storage=storage)

    if url_or_upc is not None:
        # Check a single item
        try:
            upc = extract_upc_or_passthrough(url_or_upc)
        except ValueError as exc:
            _error(str(exc))

        items = storage.load_items()
        matching = [item for item in items if item.upc == upc]
        if not matching:
            _error(f"No tracked item found with UPC: {upc}")

        item = matching[0]
        changes = tracker.check_item(item)

        if changes:
            click.echo(f"Changes for {item.label} ({item.upc}):")
            for change in changes:
                click.echo(
                    f"  {change.field}: {change.old_value} -> {change.new_value}"
                )
                if change.is_new_sale:
                    click.echo("    (New sale!)")
                elif change.is_sale_ended:
                    click.echo("    (Sale ended)")
        else:
            click.echo(f"No changes detected for {item.label} ({item.upc})")
    else:
        # Check all items
        results = tracker.check_once()
        if results:
            click.echo("Changes detected:")
            for item, changes in results:
                click.echo(f"\n  {item.label} ({item.upc}):")
                for change in changes:
                    click.echo(
                        f"    {change.field}: {change.old_value} -> {change.new_value}"
                    )
                    if change.is_new_sale:
                        click.echo("      (New sale!)")
                    elif change.is_sale_ended:
                        click.echo("      (Sale ended)")
        else:
            click.echo("No changes detected for any tracked item.")


@main.command()
@click.option(
    "--interval", "-i", default=None, type=int, help="Polling interval in seconds"
)
@click.pass_context
def run(ctx: click.Context, interval: int | None) -> None:
    """Run the polling loop (daemon mode).

    Checks all tracked items periodically for price changes.
    Default interval is 3600 seconds (1 hour).
    """
    config = _get_config(ctx)
    effective_interval = interval if interval is not None else config.poll_interval

    storage = Storage(config.data_dir)
    tracker = Tracker(storage=storage)

    click.echo(
        f"Starting polling loop (interval={effective_interval}s). "
        f"Press Ctrl+C to stop."
    )
    tracker.run(interval_seconds=effective_interval)


@main.command()
@click.pass_context
def config_cmd(ctx: click.Context) -> None:
    """Show current configuration."""
    config = _get_config(ctx)

    click.echo(f"Data Dir:        {config.data_dir}")
    click.echo(f"Log Level:       {config.log_level}")
    click.echo(f"Default Chain:   {config.default_chain}")
    click.echo(f"Default ZIP:     {config.default_zip or '(not set)'}")
    click.echo(f"Poll Interval:   {config.poll_interval}s")
    click.echo(f"Web Fetcher:     {config.use_web_fetcher}")


# The command function is named config_cmd but registered as "config"
# to avoid shadowing the config module variable.
main.add_command(config_cmd, name="config")
