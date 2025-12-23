from backend.app.models import RoleEnum, SKU, User
from backend.app.notifications import notify_price_spike, notify_stock_alert


class DummyEmail:
    sent = []


def fake_send(subject, body, recipients):
    DummyEmail.sent.append((subject, body, recipients))


def test_notify_stock_alert_triggers_email(monkeypatch, session):
    monkeypatch.setattr("backend.app.notifications._send_smtp", fake_send)
    user = User(email="ops@example.com", password_hash="x", role=RoleEnum.operator, receives_stock_alerts=True)
    session.add(user)
    sku = SKU(
        name="Test SKU",
        sku_code="TSKU",
        current_stock=5.0,
        alert_threshold_qty=10.0,
        waste_pct=0.0,
        unit_of_measure="pieces",
    )
    session.add(sku)
    session.commit()
    notify_stock_alert(session, sku)
    assert DummyEmail.sent


def test_notify_price_spike_notifies_owner(monkeypatch, session):
    DummyEmail.sent.clear()
    monkeypatch.setattr("backend.app.notifications._send_smtp", fake_send)
    owner = User(email="owner@example.com", password_hash="x", role=RoleEnum.owner)
    session.add(owner)
    sku = SKU(
        name="Pricey",
        sku_code="PRC",
        current_stock=20.0,
        alert_threshold_qty=100.0,
        waste_pct=0.0,
        unit_of_measure="pieces",
    )
    session.add(sku)
    session.commit()
    notify_price_spike(session, sku, previous_price=10.0, new_price=12.0)
    assert DummyEmail.sent
