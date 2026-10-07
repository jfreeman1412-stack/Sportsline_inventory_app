import json
import hmac
import logging
import math
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import AuditLog, SKU, ShippingMapping, SyncLog
from ..notifications import notify_stock_alert, notify_unmapped_shipping
from ..schemas import ShipStationLabelPayload

logger = logging.getLogger(__name__)
router = APIRouter(tags=["integrations"])


SHIPPING_DEDUCTED_ACTION = "shipping-deducted"


def shipstation_already_deducted(db: Session, shipstation_order_id: str | None) -> bool:
    if not shipstation_order_id:
        return False
    return (
        db.scalar(
            select(SyncLog.id).where(
                SyncLog.shipstation_order_id == shipstation_order_id,
                SyncLog.action == SHIPPING_DEDUCTED_ACTION,
            )
        )
        is not None
    )


def _record_shipping_log(
    db: Session,
    order_id: int,
    action: str,
    details: str,
    shipstation_order_id: str | None,
    timestamp: datetime | None = None,
):
    # sync_logs.shipstation_order_id is UNIQUE and acts as the dedup key, so only the
    # first successful deduction for a shipment claims it; everything else keeps the
    # ShipStation ID in the details text instead.
    if shipstation_order_id:
        details = f"[ShipStation {shipstation_order_id}] {details}"
    log = SyncLog(
        internal_order_id=order_id,
        action=action,
        details=details,
    )
    if timestamp:
        log.timestamp = timestamp
    db.add(log)
    return log


@router.post("/shipstation/label")
def shipstation_label(
    payload: ShipStationLabelPayload,
    x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
):
    if not hmac.compare_digest(x_internal_token or "", settings.internal_sync_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if shipstation_already_deducted(db, payload.shipstation_order_id):
        logger.info("Duplicate shipstation deduction skipped (%s)", payload.shipstation_order_id)
        return {"skipped": True}

    length = width = height = None
    dims = parse_dimensions_string(payload.dimensions)
    if dims:
        length, width, height = dims

    deducted = process_shipping_package(
        db,
        payload.internal_order_id,
        payload.shipstation_order_id,
        length,
        width,
        height,
        None,
        payload.dimensions,
    )
    db.commit()
    if deducted is None:
        raise HTTPException(status_code=422, detail="No shipping mapping found")
    logger.info("Shipping deduction processed for ShipStation ID %s", payload.shipstation_order_id)
    return {"deducted": deducted}


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_dimensions_string(dimensions: str) -> tuple[float | None, float | None, float | None] | None:
    if not dimensions:
        return None
    parts = [part.strip() for part in dimensions.lower().replace("×", "x").split("x") if part.strip()]
    if len(parts) < 3:
        return None
    try:
        return tuple(float(part) for part in parts[:3])
    except ValueError:
        return None


def find_shipping_mapping(
    db: Session,
    length: float | None,
    width: float | None,
    height: float | None,
    package_code: str | None = None,
    dimensions_string: str | None = None,
) -> ShippingMapping | None:
    candidates = db.scalars(select(ShippingMapping)).all()
    for mapping in candidates:
        length_match = (
            mapping.length is not None
            and length is not None
            and math.isclose(mapping.length, length, rel_tol=1e-3)
        )
        width_match = (
            mapping.width is not None
            and width is not None
            and math.isclose(mapping.width, width, rel_tol=1e-3)
        )
        height_match = (
            mapping.height is not None
            and height is not None
            and math.isclose(mapping.height, height, rel_tol=1e-3)
        )
        dims_match = length_match and width_match and height_match
        legacy_match = False
        if dimensions_string and mapping.dimensions_string:
            legacy_match = mapping.dimensions_string == dimensions_string
        if dims_match or legacy_match:
            return mapping
    return None


def _deduct_shipping_items(
    db: Session,
    mapping: ShippingMapping,
    order_id: int,
    shipstation_order_id: str | None,
    details: str,
    timestamp: datetime | None = None,
    claim_shipstation_id: bool = True,
) -> list[str]:
    try:
        items = json.loads(mapping.items_json)
    except json.JSONDecodeError:
        items = []
    deducted: list[str] = []
    for sku_code in items:
        sku = db.scalar(select(SKU).where(SKU.sku_code == sku_code))
        if not sku:
            logger.warning("Shipping mapping %s references unknown SKU %s", mapping.id, sku_code)
            continue
        previous_stock = sku.current_stock
        sku.current_stock = max(sku.current_stock - 1, 0.0)
        notify_stock_alert(db, sku, previous_stock=previous_stock)
        db.add(
            AuditLog(
                action="deduct-shipping",
                order_id=order_id,
                child_sku_code=sku.sku_code,
                child_sku_name=sku.name,
                child_unit=sku.unit_of_measure,
                quantity=1.0,
                details=f"ShipStation {shipstation_order_id}, SKU {sku.sku_code}",
            )
        )
        deducted.append(sku.sku_code)
    log = _record_shipping_log(
        db,
        order_id,
        SHIPPING_DEDUCTED_ACTION,
        f"{details} (deducted {len(deducted)} SKUs for mapping {_mapping_label(mapping)})",
        shipstation_order_id,
        timestamp=timestamp,
    )
    # Older rows (e.g. unmapped events logged before this fix) may already hold the key.
    if (
        claim_shipstation_id
        and shipstation_order_id
        and db.scalar(
            select(SyncLog.id).where(SyncLog.shipstation_order_id == shipstation_order_id)
        )
        is None
    ):
        log.shipstation_order_id = shipstation_order_id
    return deducted


def _mapping_label(mapping: ShippingMapping) -> str:
    if mapping.dimensions_string:
        return mapping.dimensions_string
    return f"{mapping.length}x{mapping.width}x{mapping.height}"


def process_shipping_package(
    db: Session,
    order_id: int,
    shipstation_order_id: str | None,
    length: float | None,
    width: float | None,
    height: float | None,
    package_code: str | None,
    dimensions_string: str | None,
    timestamp: datetime | None = None,
    package_index: int = 0,
) -> list[str] | None:
    """Deduct the shipping SKUs for one package. Returns the deducted SKU codes, or None
    when no mapping matched."""
    mapping = find_shipping_mapping(
        db, length, width, height, package_code, dimensions_string
    )
    dims_text = dimensions_string or f"{length or '-'}x{width or '-'}x{height or '-'}"
    if not mapping:
        _record_shipping_log(
            db,
            order_id,
            "shipping-unmapped",
            f"Dimensions {dims_text} missing mapping",
            shipstation_order_id,
            timestamp=timestamp,
        )
        notify_unmapped_shipping(
            db,
            order_id,
            shipstation_order_id,
            dims_text,
        )
        return None
    return _deduct_shipping_items(
        db,
        mapping,
        order_id,
        shipstation_order_id,
        f"ShipStation dims {dims_text}",
        timestamp=timestamp,
        # Only the first package of a shipment claims the unique dedup key.
        claim_shipstation_id=package_index == 0,
    )


def extract_package_data(order: dict) -> list[dict]:
    packages = []
    for shipment in order.get("shipments", []):
        for pkg in shipment.get("packages", []):
            dims = pkg.get("dimensions", {}) or {}
            length = _to_float(pkg.get("length") or dims.get("length"))
            width = _to_float(pkg.get("width") or dims.get("width"))
            height = _to_float(pkg.get("height") or dims.get("height"))
            package_code = pkg.get("packageCode") or pkg.get("code")
            dimension_string = pkg.get("dimensions") or pkg.get("dimension")
            if not isinstance(dimension_string, str):
                dimension_string = None
            packages.append(
                {
                    "length": length,
                    "width": width,
                    "height": height,
                    "package_code": package_code,
                    "dimension_string": dimension_string,
                }
            )
    if not packages:
        # ShipStation's v1 /orders response carries one set of dimensions on the order
        # itself rather than a shipments/packages list.
        dims = order.get("dimensions") or {}
        length, width, height = (_to_float(dims.get(k)) for k in ("length", "width", "height"))
        if length and width and height:
            packages.append(
                {
                    "length": length,
                    "width": width,
                    "height": height,
                    "package_code": order.get("packageCode"),
                    "dimension_string": None,
                }
            )
    return packages


def fetch_shipstation_order(order_number: str | int) -> dict | None:
    if not settings.shipstation_api_key or not settings.shipstation_api_secret:
        return None
    url = "https://ssapi.shipstation.com/orders"
    params = {"orderNumber": order_number}
    auth = (settings.shipstation_api_key, settings.shipstation_api_secret)
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params=params, auth=auth)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError:
        return None
    for order in data.get("orders", []):
        if str(order.get("orderNumber")) == str(order_number):
            return order
    return None
