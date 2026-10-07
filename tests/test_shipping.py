import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.app.models import SKU, ShippingMapping
from backend.app.routers.shipstation import shipstation_label
from backend.app.schemas import ShipStationLabelPayload
from backend.app.config import settings


@pytest.fixture(autouse=True)
def disable_notifications(monkeypatch):
    monkeypatch.setattr("backend.app.routers.shipstation.notify_stock_alert", lambda *args, **kwargs: None)
    monkeypatch.setattr("backend.app.routers.shipstation.notify_unmapped_shipping", lambda *args, **kwargs: None)


def test_shipstation_mapping_deducts_stock(session):
    sku = SKU(name="Mailer", sku_code="MAILER", current_stock=10.0, waste_pct=0.0)
    session.add(sku)
    session.commit()
    mapping = ShippingMapping(dimensions_string="12x9x0.25", items_json=json.dumps(["MAILER"]))
    session.add(mapping)
    session.commit()
    payload = ShipStationLabelPayload(
        internal_order_id=100,
        shipstation_order_id="SS-123",
        dimensions="12x9x0.25",
    )
    result = shipstation_label(
        payload,
        x_internal_token=settings.internal_sync_token,
        db=session,
    )
    assert result["deducted"] == ["MAILER"]
    session.refresh(sku)
    assert sku.current_stock == 9.0


def test_shipstation_unmapped(session):
    payload = ShipStationLabelPayload(
        internal_order_id=200,
        shipstation_order_id="SS-999",
        dimensions="99x99x99",
    )
    with pytest.raises(HTTPException):
        shipstation_label(
            payload,
            x_internal_token=settings.internal_sync_token,
            db=session,
        )
    mapping = session.scalar(select(ShippingMapping).where(ShippingMapping.dimensions_string == "99x99x99"))
    assert mapping is None


def test_multi_package_shipment_does_not_violate_unique_key(session):
    from backend.app.models import SyncLog
    from backend.app.routers.shipstation import process_shipping_package

    session.add(SKU(name="Mailer", sku_code="MAILER", current_stock=10.0, waste_pct=0.0))
    session.add(ShippingMapping(length=12, width=9, height=0.25, items_json=json.dumps(["MAILER"])))
    session.commit()
    for index in range(2):
        process_shipping_package(session, 1, "SS-1", 12, 9, 0.25, None, None, package_index=index)
    session.commit()  # used to raise IntegrityError on the second package
    claimed = session.scalars(select(SyncLog).where(SyncLog.shipstation_order_id == "SS-1")).all()
    assert len(claimed) == 1


def test_webhook_retry_after_unmapped_is_not_skipped(session):
    payload = ShipStationLabelPayload(internal_order_id=7, shipstation_order_id="SS-7", dimensions="5x5x5")
    with pytest.raises(HTTPException):
        shipstation_label(payload, x_internal_token=settings.internal_sync_token, db=session)
    sku = SKU(name="Box", sku_code="BOX", current_stock=3.0, waste_pct=0.0)
    session.add(sku)
    session.add(ShippingMapping(dimensions_string="5x5x5", items_json=json.dumps(["BOX"])))
    session.commit()
    result = shipstation_label(payload, x_internal_token=settings.internal_sync_token, db=session)
    assert result["deducted"] == ["BOX"]
