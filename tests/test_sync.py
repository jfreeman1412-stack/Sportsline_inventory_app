from datetime import datetime

import pytest
from sqlalchemy import select

from backend.app.models import SKU, SKURecipe, SyncLog
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
    sync._process_print_order(session, order)
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
