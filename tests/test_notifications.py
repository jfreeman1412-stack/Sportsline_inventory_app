from backend.app.models import RoleEnum, SKU, User
from backend.app.notifications import notify_price_spike, notify_stock_alert


class DummyEmail:
    sent = []


def fake_send(subject, body, recipients, smtp_config=None):
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


def test_stock_alert_only_fires_when_crossing_threshold(monkeypatch, session):
    DummyEmail.sent.clear()
    monkeypatch.setattr("backend.app.notifications._send_smtp", fake_send)
    session.add(User(email="ops@example.com", password_hash="x", role=RoleEnum.operator))
    sku = SKU(name="Roll", sku_code="ROLL", current_stock=5.0, alert_threshold_qty=10.0)
    session.add(sku)
    session.commit()
    # Already below threshold before this deduction: no repeat email.
    notify_stock_alert(session, sku, previous_stock=6.0)
    assert not DummyEmail.sent
    # Crossed the threshold with this deduction: one email.
    notify_stock_alert(session, sku, previous_stock=12.0)
    assert len(DummyEmail.sent) == 1


def test_smtp_failure_does_not_raise(monkeypatch, session):
    import smtplib

    from backend.app.notifications import _send_smtp
    from backend.app.services.app_settings import get_app_settings

    def boom(*args, **kwargs):
        raise smtplib.SMTPConnectError(421, "down")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    _send_smtp("subject", "body", ["a@example.com"], get_app_settings(session))
