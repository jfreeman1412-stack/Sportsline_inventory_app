from datetime import datetime

import pytest
from sqlalchemy import select

from backend.app.legacy_sync import group_orders
from backend.app.models import AddOnMapping, Product, ProductRecipe, SKU, SKURecipe, SyncLog
from backend.app.routers import sync
from backend.app.schemas import OrderLineItemPayload, OrderPayload


@pytest.fixture(autouse=True)
def disable_notifications(monkeypatch):
    monkeypatch.setattr(sync, "notify_stock_alert", lambda *args, **kwargs: None)


def create_bom(session):
    parent = SKU(
        name="Bundle",
        sku_code="BUNDLE",
        current_stock=0.0,
        waste_pct=0.0,
        unit_of_measure="pieces",
    )
    child = SKU(
        name="Roll",
        sku_code="ROLL",
        current_stock=100.0,
        waste_pct=5.0,
        unit_of_measure="feet",
    )
    session.add_all([parent, child])
    session.commit()
    recipe = SKURecipe(parent_sku_id=parent.sku_id, child_sku_id=child.sku_id, qty_used=2.0)
    session.add(recipe)
    session.commit()
    session.refresh(child)
    return parent, child


def test_process_print_order_deducts_stock(session):
    parent, child = create_bom(session)
    order = OrderPayload(
        order_id=1,
        order_date=datetime.utcnow(),
        order_open_status=40,
        line_items=[OrderLineItemPayload(cart_sku="BUNDLE", cart_qty=3)],
    )
    sync._process_print_order(session, order, {})
    session.flush()
    session.refresh(child)
    assert child.current_stock < 100.0


def test_handle_orders_deduplication(session):
    parent, _ = create_bom(session)
    payload = OrderPayload(
        order_id=2,
        order_date=datetime.utcnow(),
        order_open_status=40,
        line_items=[OrderLineItemPayload(cart_sku="BUNDLE", cart_qty=1)],
    )
    processed_first = sync._handle_orders(session, [payload], action_label="poller-sync")
    session.flush()
    processed_second = sync._handle_orders(session, [payload], action_label="poller-sync")
    assert processed_first == 1
    assert processed_second == 0
    assert session.scalar(select(SyncLog)).internal_order_id == 2


def _order(order_id, status, sku="BUNDLE", qty=1, add_ons=None):
    return OrderPayload(
        order_id=order_id,
        order_date=datetime.utcnow(),
        order_open_status=status,
        line_items=[OrderLineItemPayload(cart_sku=sku, cart_qty=qty, add_ons=add_ons or [])],
    )


def test_product_bom_deducts_once(session):
    roll = SKU(name="Roll", sku_code="ROLL", current_stock=100.0, waste_pct=0.0)
    product = Product(product_code="8X10", name="8x10 Print")
    session.add_all([roll, product])
    session.commit()
    session.add(ProductRecipe(parent_product_id=product.product_id, child_sku_id=roll.sku_id, qty_used=10.0))
    session.commit()
    sync._process_print_order(session, _order(10, 40, sku="8X10", qty=2), {})
    session.flush()
    assert roll.current_stock == 80.0
    # No bogus "missing parent" log from falling through to the legacy SKU path.
    assert session.scalar(select(SyncLog).where(SyncLog.action == "print-missing-parent")) is None


def test_unknown_cart_sku_does_not_crash(session):
    sync._process_print_order(session, _order(11, 40, sku="NOPE"), {})
    session.flush()
    assert session.scalar(select(SyncLog).where(SyncLog.action == "print-missing-parent"))


def test_addons_apply_to_product_lines(session):
    magnet = SKU(name="Magnet", sku_code="MAG", current_stock=10.0, waste_pct=0.0)
    product = Product(product_code="8X10", name="8x10 Print")
    session.add_all([magnet, product, AddOnMapping(add_on_name="Magnet back", sku_code="MAG", quantity=1)])
    session.commit()
    session.add(ProductRecipe(parent_product_id=product.product_id, child_sku_id=magnet.sku_id, qty_used=0.0))
    session.commit()
    sync._process_print_order(
        session, _order(12, 40, sku="8X10", add_ons=["Magnet Back"]), sync._load_addon_mappings(session)
    )
    session.flush()
    assert magnet.current_stock == 9.0


def test_force_sync_does_not_rededuct_poller_orders(session):
    _, child = create_bom(session)
    assert sync._handle_orders(session, [_order(3, 40)], action_label="poller-sync") == 1
    stock_after_first = child.current_stock
    assert sync._handle_orders(session, [_order(3, 40)], action_label="force-sync") == 0
    assert child.current_stock == stock_after_first


def test_shipped_status_still_processed_after_printing(session, monkeypatch):
    create_bom(session)
    calls = []
    monkeypatch.setattr(sync, "fetch_shipstation_order", lambda order_id: calls.append(order_id))
    sync._handle_orders(session, [_order(4, 40)], action_label="poller-sync")
    sync._handle_orders(session, [_order(4, 39)], action_label="poller-sync")
    assert calls == [4]


def test_group_orders_splits_status_and_dedupes_lines():
    when = datetime(2026, 1, 1)
    rows = [
        {"order_id": 5, "order_date": when, "order_open_status": 40, "cart_id": 1, "cart_sku": "A", "cart_qty": 2},
        {"order_id": 5, "order_date": when, "order_open_status": 40, "cart_id": 1, "cart_sku": "A", "cart_qty": 2},
        {"order_id": 5, "order_date": when, "order_open_status": 39, "cart_id": 1, "cart_sku": "A", "cart_qty": 2},
    ]
    grouped = group_orders(rows, {})
    assert sorted(g["order_open_status"] for g in grouped) == [39, 40]
    assert all(len(g["line_items"]) == 1 for g in grouped)


def test_shipping_retries_until_shipstation_has_packages(session, monkeypatch):
    import json

    from backend.app.models import ShippingMapping

    mailer = SKU(name="Mailer", sku_code="MAILER", current_stock=10.0, waste_pct=0.0)
    session.add_all([mailer, ShippingMapping(length=12, width=9, height=1, items_json=json.dumps(["MAILER"]))])
    session.commit()
    monkeypatch.setattr("backend.app.routers.shipstation.notify_stock_alert", lambda *a, **k: None)

    responses = {"order": None}
    monkeypatch.setattr(sync, "fetch_shipstation_order", lambda order_id: responses["order"])
    sync._handle_orders(session, [_order(20, 39)], action_label="poller-sync")
    assert mailer.current_stock == 10.0
    assert session.scalar(select(SyncLog).where(SyncLog.action == "shipping-label-pending"))

    # Label shows up later (v1 order-level dimensions); the next sync deducts it once.
    responses["order"] = {"orderId": 555, "dimensions": {"length": 12, "width": 9, "height": 1}}
    assert sync._retry_pending_shipments(session) == 1
    assert mailer.current_stock == 9.0
    assert sync._retry_pending_shipments(session) == 0
    assert mailer.current_stock == 9.0
