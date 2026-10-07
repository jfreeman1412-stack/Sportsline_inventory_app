from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import ensure_manager_or_owner
from ..database import get_db
from ..models import AuditLog, PurchaseLog, SKU
from ..services.audit import DEDUCTION_ACTIONS
from ..services.export import csv_response

BASE_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
router = APIRouter(prefix="/analytics", tags=["analytics"])
templates = Jinja2Templates(directory=str(BASE_TEMPLATES))

WINDOW_CHOICES = (30, 90, 365)
REORDER_SOON_DAYS = 14


def _usage_rows(db: Session, days: int) -> list[dict]:
    """Per raw material: what was used in the window, how long current stock lasts."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    used = dict(
        db.execute(
            select(AuditLog.child_sku_code, func.sum(AuditLog.quantity))
            .where(AuditLog.action.in_(DEDUCTION_ACTIONS))
            .where(AuditLog.timestamp >= cutoff)
            .group_by(AuditLog.child_sku_code)
        ).all()
    )
    rows = []
    for sku in db.scalars(select(SKU).order_by(SKU.name)):
        total = float(used.get(sku.sku_code) or 0.0)
        per_day = total / days
        # Deductions already include waste: total = base * (1 + waste%), so the waste
        # share of what was used is total * waste / (100 + waste).
        waste = total * (sku.waste_pct or 0.0) / (100 + (sku.waste_pct or 0.0))
        days_left = sku.current_stock / per_day if per_day > 0 else None
        rows.append(
            {
                "sku": sku,
                "used": total,
                "per_day": per_day,
                "waste": waste,
                "days_left": days_left,
                "reorder_soon": days_left is not None and days_left <= REORDER_SOON_DAYS,
            }
        )
    rows.sort(key=lambda r: (r["days_left"] is None, r["days_left"] or 0, -r["used"]))
    return rows


def _spend(db: Session, days: int):
    cutoff = date.today() - timedelta(days=days)
    logs = db.scalars(select(PurchaseLog).where(PurchaseLog.purchase_date >= cutoff)).all()
    by_supplier: dict[str, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    for log in logs:
        amount = (log.quantity or 0.0) * (log.price or 0.0)
        supplier = log.supplier_name or log.sku.vendor_name or "Unknown supplier"
        by_supplier[supplier] += amount
        by_month[log.purchase_date.strftime("%Y-%m")] += amount
    suppliers = sorted(by_supplier.items(), key=lambda item: item[1], reverse=True)
    months = sorted(by_month.items())
    return suppliers, months, sum(by_supplier.values())


def _price_changes(db: Session) -> list[dict]:
    """Latest vs previous purchase price per raw material, biggest increases first."""
    by_sku: dict[int, list[PurchaseLog]] = defaultdict(list)
    for log in db.scalars(
        select(PurchaseLog).order_by(PurchaseLog.purchase_date.desc(), PurchaseLog.id.desc())
    ):
        if len(by_sku[log.sku_id]) < 2:
            by_sku[log.sku_id].append(log)
    changes = []
    for latest, *rest in (logs for logs in by_sku.values()):
        if not rest or not rest[0].price:
            continue
        pct = (latest.price - rest[0].price) / rest[0].price * 100
        changes.append({"sku": latest.sku, "previous": rest[0].price, "latest": latest.price, "pct": pct, "date": latest.purchase_date})
    changes.sort(key=lambda c: c["pct"], reverse=True)
    return changes


@router.get("/")
def analytics_page(
    request: Request,
    days: int = Query(30),
    format: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    days = days if days in WINDOW_CHOICES else 30
    usage = _usage_rows(db, days)
    if format == "csv":
        return csv_response(
            f"usage-last-{days}-days.csv",
            ["sku_code", "name", "unit", "current_stock", "used", "avg_per_day", "est_waste", "days_of_stock_left"],
            (
                [
                    r["sku"].sku_code, r["sku"].name, r["sku"].unit_of_measure, r["sku"].current_stock,
                    round(r["used"], 2), round(r["per_day"], 3), round(r["waste"], 2),
                    "" if r["days_left"] is None else round(r["days_left"], 1),
                ]
                for r in usage
            ),
        )
    suppliers, months, total_spend = _spend(db, days)
    return templates.TemplateResponse(
        "analytics/index.html",
        {
            "request": request,
            "current_user": current_user,
            "title": "Analytics",
            "days": days,
            "window_choices": WINDOW_CHOICES,
            "usage": usage,
            "reorder_soon": [r for r in usage if r["reorder_soon"]],
            "total_waste_rows": [r for r in usage if r["waste"] > 0],
            "suppliers": suppliers,
            "months": months,
            "total_spend": total_spend,
            "price_changes": _price_changes(db)[:15],
            "reorder_soon_days": REORDER_SOON_DAYS,
        },
    )
