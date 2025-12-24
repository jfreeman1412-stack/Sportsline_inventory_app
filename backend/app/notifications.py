from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy import select

from .config import settings
from .models import RoleEnum, SKU, User

logger = logging.getLogger(__name__)


def _send_smtp(subject: str, body: str, recipients: list[str]) -> None:
    recipients = [email for email in recipients if email]
    if not recipients:
        logger.debug("No recipients for %s", subject)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
    with server:
        if settings.smtp_port != 465:
            server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


def _send_twilio(to_number: str, body: str) -> None:
    if not (settings.twilio_sid and settings.twilio_token and settings.twilio_from_number):
        return
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
    data = {
        "From": settings.twilio_from_number,
        "To": to_number,
        "Body": body,
    }
    with httpx.Client(timeout=10.0, auth=(settings.twilio_sid, settings.twilio_token)) as client:
        client.post(url, data=data)


def _stock_recipients(db) -> list[str]:
    return [
        user.email
        for user in db.scalars(select(User).where(User.receives_stock_alerts == 1)).all()
    ]


def _owner_recipient(db) -> str | None:
    owner = db.scalar(select(User).where(User.role == RoleEnum.owner))
    return owner.email if owner else None


def notify_stock_alert(db, sku: SKU) -> None:
    if not sku.alert_threshold_qty or sku.current_stock >= sku.alert_threshold_qty:
        return
    recipients = _stock_recipients(db)
    if not recipients:
        return
    subject = f"[Inventory] Low stock alert: {sku.name}"
    body = f"SKU {sku.name} ({sku.sku_code}) is below threshold ({sku.current_stock} < {sku.alert_threshold_qty})."
    _send_smtp(subject, body, recipients)


def notify_price_spike(db, sku: SKU, previous_price: float, new_price: float) -> None:
    owner_email = _owner_recipient(db)
    if not owner_email:
        return
    subject = f"[Inventory] Price spike detected for {sku.name}"
    body = (
        f"New purchase price {new_price} exceeded 10% above previous price {previous_price} "
        f"for SKU {sku.sku_code}. Please review supplier data."
    )
    _send_smtp(subject, body, [owner_email])


def notify_unmapped_shipping(
    db, order_id: int, shipstation_order_id: str | None, dimensions: str
) -> None:
    recipients = _stock_recipients(db)
    if not recipients:
        return
    subject = "[Inventory] Shipping mapping missing"
    body = (
        f"Order {order_id} (ShipStation ID {shipstation_order_id}) has dimensions {dimensions} "
        "with no matching shipping mapping. Please add it in the dashboard."
    )
    _send_smtp(subject, body, recipients)
