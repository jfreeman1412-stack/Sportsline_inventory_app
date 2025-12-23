from __future__ import annotations

from datetime import datetime, timedelta
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import ensure_manager_or_owner
from ..config import settings
from ..database import get_db
from ..legacy_sync import fetch_order_rows, group_orders, get_legacy_product_name
from ..models import AuditLog, Product, ProductRecipe, SKU, SKURecipe, SyncLog, User
from ..notifications import notify_stock_alert
from ..schemas import OrderPayload, SyncPayload
from .shipstation import (
    extract_package_data,
    fetch_shipstation_order,
    process_shipping_package,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["internal"])


def _record_sync(
    db: Session,
    order_id: int,
    action: str,
    details: str,
    shipstation_order_id: str | None = None,
    timestamp: datetime | None = None,
) -> None:
    log = SyncLog(
        internal_order_id=order_id,
        action=action,
        details=details,
        shipstation_order_id=shipstation_order_id,
    )
    if timestamp:
        log.timestamp = timestamp
    db.add(log)


def _deduct_child(
    db: Session,
    order_id: int,
    child: SKU,
    base_qty: float,
    source_label: str,
    product_code: str | None = None,
    product_name: str | None = None,
    product_quantity: float | None = None,
) -> None:
    waste_multiplier = 1 + (child.waste_pct or 0.0) / 100
    deduction = base_qty * waste_multiplier
    child.current_stock = max(child.current_stock - deduction, 0.0)
    notify_stock_alert(db, child)
    db.add(
        AuditLog(
            action="deduct-print",
            order_id=order_id,
            child_sku_code=child.sku_code,
            child_sku_name=child.name,
            child_unit=child.unit_of_measure,
            quantity=deduction,
            product_code=product_code,
            product_name=product_name,
            product_quantity=product_quantity,
            details=f"Order {order_id} ({source_label}), child {child.sku_code}, qty {deduction:.2f}",
        )
    )


def _process_print_order(db: Session, order: OrderPayload) -> None:
    for line in order.line_items:
        product = db.scalar(select(Product).where(Product.product_code == line.cart_sku))
        line_qty = float(line.cart_qty)
        legacy_product_name = get_legacy_product_name(line.cart_sku)
        product_display_name = product.name if product else legacy_product_name or line.cart_sku
        product_code_value = line.cart_sku
        if product:
            recipes = (
                db.scalars(
                    select(ProductRecipe).where(
                        ProductRecipe.parent_product_id == product.product_id
                    )
                )
                .all()
            )
            if not recipes:
                _record_sync(
                    db,
                    order.order_id,
                    "product-no-recipes",
                    f"Product {product.product_code} has no BOM recipes, skipping",
                    timestamp=order.order_date,
                )
                continue
            for recipe in recipes:
                _deduct_child(
                    db,
                    order.order_id,
                    recipe.child,
                    recipe.qty_used * line.cart_qty,
                    f"product {product.product_code}",
                    product_code=product_code_value,
                    product_name=product_display_name,
                    product_quantity=line_qty,
                )
            continue
        sku = db.scalar(select(SKU).where(SKU.sku_code == line.cart_sku))
        if not sku:
            _record_sync(
                db,
                order.order_id,
                "print-missing-parent",
                f"Missing SKU {line.cart_sku}, skipping",
                timestamp=order.order_date,
            )
            continue
        recipes = (
            db.scalars(select(SKURecipe).where(SKURecipe.parent_sku_id == sku.sku_id)).all()
        )
        if not recipes:
            _record_sync(
                db,
                order.order_id,
                "print-no-recipes",
                f"SKU {sku.sku_code} has no BOM recipes, skipping",
                timestamp=order.order_date,
            )
            continue
        for recipe in recipes:
            _deduct_child(
                db,
                order.order_id,
                recipe.child,
                recipe.qty_used * line.cart_qty,
                f"SKU {sku.sku_code}",
                product_code=product_code_value,
                product_name=product_display_name,
                product_quantity=line_qty,
            )
    _record_sync(
        db,
        order.order_id,
        "print-deducted",
        f"Processed {len(order.line_items)} line items",
        timestamp=order.order_date,
    )


def _process_shipping_wait(db: Session, order: OrderPayload) -> None:
    _record_sync(
        db,
        order.order_id,
        "shipping-awaiting-label",
        f"Status 39, waiting for ShipStation label. Items={len(order.line_items)}",
        timestamp=order.order_date,
    )
    ship_order = fetch_shipstation_order(order.order_id)
    if not ship_order:
        return
    shipstation_order_id = str(ship_order.get("orderId") or ship_order.get("orderKey") or order.order_id)
    for pkg in extract_package_data(ship_order):
        process_shipping_package(
            db,
            order.order_id,
            shipstation_order_id,
            pkg.get("length"),
            pkg.get("width"),
            pkg.get("height"),
            pkg.get("package_code"),
            pkg.get("dimension_string"),
            timestamp=order.order_date,
        )


def _handle_orders(db: Session, orders: list[OrderPayload], action_label: str) -> int:
    processed = 0
    for order in orders:
        existing = db.scalar(
            select(SyncLog).where(
                SyncLog.internal_order_id == order.order_id,
                SyncLog.action == action_label,
            )
        )
        if existing:
            continue
        _record_sync(
            db,
            order.order_id,
            action_label,
            f"status={order.order_open_status}, lines={len(order.line_items)}",
            timestamp=order.order_date,
        )
        if order.order_open_status == 40:
            _process_print_order(db, order)
            processed += 1
        elif order.order_open_status == 39:
            _process_shipping_wait(db, order)
        else:
            _record_sync(
                db,
                order.order_id,
                "unknown-status",
                f"Status {order.order_open_status} not handled",
                timestamp=order.order_date,
            )
    return processed


def _latest_sync_timestamp(db: Session) -> datetime:
    last = db.scalar(select(func.max(SyncLog.timestamp)))
    if last:
        return last
    fallback_seconds = settings.poll_interval_seconds or 90
    return datetime.utcnow() - timedelta(seconds=fallback_seconds)


def _build_orders_from_rows(rows: list[dict]) -> list[OrderPayload]:
    grouped = group_orders(rows)
    return [
        OrderPayload(
            order_id=item["order_id"],
            order_date=item["order_date"],
            order_open_status=item["order_open_status"],
            line_items=item["line_items"],
        )
        for item in grouped
    ]


@router.post("/internal-sync")
def internal_sync(
    payload: SyncPayload,
    x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
):
    if x_internal_token != settings.internal_sync_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    processed = 0
    try:
        with db.begin():
            processed = _handle_orders(db, payload.orders, action_label="poller-sync")
    except IntegrityError:
        db.rollback()
        logger.exception("Failed to commit sync batch")
        raise HTTPException(status_code=500, detail="Failed to record sync")
    return {"processed_orders": processed}


@router.post("/sync/force")
def force_sync(
    db: Session = Depends(get_db),
    _: User = Depends(ensure_manager_or_owner),
):
    since = _latest_sync_timestamp(db)
    rows = fetch_order_rows(since)
    if not rows:
        return PlainTextResponse("No new orders found")
    orders = _build_orders_from_rows(rows)
    processed = 0
    try:
        processed = _handle_orders(db, orders, action_label="force-sync")
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.exception("Failed to commit manual sync")
        raise HTTPException(status_code=500, detail="Force sync failed")
    msg = f"Force sync complete, {processed} orders processed."
    return PlainTextResponse(msg)
