"""Change detection for price snapshots."""

from dataclasses import dataclass

from krogetter.models import PriceSnapshot


@dataclass
class ChangeEvent:
    """Represents a detected change in a product's price or promo."""

    field: str  # "regular", "promo", "promo_description", "offer_*", or "initial"
    old_value: str  # string representation of old value
    new_value: str  # string representation of new value
    is_new_sale: bool  # True if item went from not-on-sale to on-sale
    is_sale_ended: bool  # True if item went from on-sale to not-on-sale


def _on_sale(snap: PriceSnapshot | None) -> bool:
    """Check if a snapshot represents an on-sale or offer state. None means no data."""
    if snap is None:
        return False
    return snap.is_on_sale or snap.has_offer


def detect_change(
    old: PriceSnapshot | None,
    new: PriceSnapshot,
) -> list[ChangeEvent]:
    """Compare two price snapshots and return a list of changes.

    Returns an empty list if nothing changed. Detects changes to:
    - regular price
    - promo price
    - promo_description (via synthetic_description)

    Also flags is_new_sale (went from not-on-sale to on-sale) and
    is_sale_ended (went from on-sale to not-on-sale).

    If old is None (first check), returns a single ChangeEvent with
    field="initial" if the item is on sale.
    """
    # First check: no previous snapshot
    if old is None:
        if new.is_on_sale or new.has_offer:
            old_str = "no data"
            new_desc = new.synthetic_description or "on sale"
            return [
                ChangeEvent(
                    field="initial",
                    old_value=old_str,
                    new_value=new_desc,
                    is_new_sale=True,
                    is_sale_ended=False,
                )
            ]
        return []

    changes: list[ChangeEvent] = []

    # Detect new sale / sale ended
    was_on_sale = _on_sale(old)
    now_on_sale = _on_sale(new)
    is_new_sale = (not was_on_sale) and now_on_sale
    is_sale_ended = was_on_sale and (not now_on_sale)

    # Regular price change
    if old.regular != new.regular:
        changes.append(
            ChangeEvent(
                field="regular",
                old_value=str(old.regular),
                new_value=str(new.regular),
                is_new_sale=is_new_sale,
                is_sale_ended=is_sale_ended,
            )
        )

    # Promo price change
    if old.promo != new.promo:
        changes.append(
            ChangeEvent(
                field="promo",
                old_value=str(old.promo),
                new_value=str(new.promo),
                is_new_sale=is_new_sale,
                is_sale_ended=is_sale_ended,
            )
        )

    # Promo description change (use synthetic_description which captures both
    # explicit promo_description and computed description)
    old_desc = old.synthetic_description or ""
    new_desc = new.synthetic_description or ""
    if old_desc != new_desc:
        changes.append(
            ChangeEvent(
                field="promo_description",
                old_value=old_desc,
                new_value=new_desc,
                is_new_sale=is_new_sale,
                is_sale_ended=is_sale_ended,
            )
        )

    # Offer template change
    old_offer_template = old.offer_template or ""
    new_offer_template = new.offer_template or ""
    if old_offer_template != new_offer_template:
        changes.append(
            ChangeEvent(
                field="offer_template",
                old_value=old_offer_template,
                new_value=new_offer_template,
                is_new_sale=is_new_sale,
                is_sale_ended=is_sale_ended,
            )
        )

    # Offer start date change
    old_offer_start = old.offer_start or ""
    new_offer_start = new.offer_start or ""
    if old_offer_start != new_offer_start:
        changes.append(
            ChangeEvent(
                field="offer_start",
                old_value=old_offer_start,
                new_value=new_offer_start,
                is_new_sale=is_new_sale,
                is_sale_ended=is_sale_ended,
            )
        )

    # Offer end date change
    old_offer_end = old.offer_end or ""
    new_offer_end = new.offer_end or ""
    if old_offer_end != new_offer_end:
        changes.append(
            ChangeEvent(
                field="offer_end",
                old_value=old_offer_end,
                new_value=new_offer_end,
                is_new_sale=is_new_sale,
                is_sale_ended=is_sale_ended,
            )
        )

    # Defensive: unreachable given current is_on_sale definition (if sale status
    # changed, at least one of regular/promo must have changed), but kept as a
    # safety net in case is_on_sale logic evolves.
    if is_new_sale and not changes:
        changes.append(
            ChangeEvent(
                field="sale_status",
                old_value="not on sale",
                new_value="on sale",
                is_new_sale=True,
                is_sale_ended=False,
            )
        )
    elif is_sale_ended and not changes:
        changes.append(
            ChangeEvent(
                field="sale_status",
                old_value="on sale",
                new_value="not on sale",
                is_new_sale=False,
                is_sale_ended=True,
            )
        )

    return changes
