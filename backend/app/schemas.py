from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OrderLineItemPayload(BaseModel):
    cart_sku: str
    cart_qty: float = Field(gt=0)
    cart_id: int | None = None
    add_ons: list[str] = Field(default_factory=list)


class OrderPayload(BaseModel):
    order_id: int
    order_date: datetime
    order_open_status: int
    line_items: list[OrderLineItemPayload]


class SyncPayload(BaseModel):
    orders: list[OrderPayload]
    triggered_at: datetime


class ShipStationLabelPayload(BaseModel):
    internal_order_id: int
    shipstation_order_id: str
    dimensions: str
