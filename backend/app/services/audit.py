from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditLog, SKU, User

# Actions written by the order sync; everything else in audit_logs is a person's edit.
DEDUCTION_ACTIONS = ("deduct-print", "deduct-shipping")


def snapshot(obj, fields: tuple[str, ...]) -> dict:
    return {field: getattr(obj, field) for field in fields}


def describe_changes(before: dict, after: dict) -> str:
    parts = []
    for field, old in before.items():
        new = after.get(field)
        if old != new:
            parts.append(f"{field.replace('_', ' ')}: {_fmt(old)} → {_fmt(new)}")
    return "; ".join(parts)


def _fmt(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def record_change(
    db: Session,
    user: User | None,
    action: str,
    details: str,
    sku: SKU | None = None,
    quantity: float | None = None,
    product_code: str | None = None,
    product_name: str | None = None,
) -> None:
    """Add an audit row for a manual change. The caller commits."""
    db.add(
        AuditLog(
            user_id=user.user_id if user else None,
            action=action,
            child_sku_code=sku.sku_code if sku else None,
            child_sku_name=sku.name if sku else None,
            child_unit=sku.unit_of_measure if sku else None,
            quantity=quantity,
            product_code=product_code,
            product_name=product_name,
            details=details,
        )
    )
