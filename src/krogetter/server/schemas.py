"""Pydantic v2 models for request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ItemCreateRequest(BaseModel):
    url: str
    label: str | None = None
    zip_code: str | None = None
    delivery: bool = False
    store_id: str | None = None


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    upc: str
    label: str
    url: str
    zip_code: str
    modality: str
    location_id: str | None
    chain: str
    added_at: str


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    regular: float
    promo: float
    promo_description: str | None
    checked_at: str
    offer_template: str | None
    offer_start: str | None
    offer_end: str | None
    fulfillment_price_string: str | None
    available: bool
    inventory_level: str | None
    current_price: float
    effective_unit_price: float | None
    is_on_sale: bool
    has_offer: bool
    savings: float | None
    savings_percent: float | None
    synthetic_description: str | None


class ItemWithLatestResponse(ItemResponse):
    latest: SnapshotResponse | None


class ChangeEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field: str
    old_value: str
    new_value: str
    is_new_sale: bool
    is_sale_ended: bool


class CheckResultResponse(BaseModel):
    upc: str
    changes: list[ChangeEventResponse]
    latest: SnapshotResponse | None


class HealthResponse(BaseModel):
    status: str
