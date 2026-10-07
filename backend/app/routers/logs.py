from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import ensure_manager_or_owner
from ..database import get_db
from ..legacy_sync import get_order_employee
from ..models import AuditLog, SyncLog, User
from ..services.audit import DEDUCTION_ACTIONS
from ..services.export import csv_response

BASE_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
router = APIRouter(prefix="/logs", tags=["logs"])
templates = Jinja2Templates(directory=str(BASE_TEMPLATES))


def _format_product_label(log: AuditLog) -> str:
    label = log.product_name or log.child_sku_name or log.child_sku_code or "Item"
    qty = log.product_quantity
    if label and qty:
        try:
            qty_value = float(qty)
            qty_text = int(qty_value) if qty_value.is_integer() else qty_value
        except (TypeError, ValueError):
            qty_text = qty
        return f"{qty_text} x {label}"
    return label


@router.get("/deductions")
def deduction_log(
    request: Request,
    order_number: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    query = select(AuditLog).where(AuditLog.order_id.is_not(None))
    query = query.where(AuditLog.action.in_(["deduct-print", "deduct-shipping"]))
    if order_number:
        query = query.where(AuditLog.order_id == order_number)
    query = query.order_by(AuditLog.timestamp.desc())
    logs = db.scalars(query.limit(400)).all()

    groups: OrderedDict[tuple[int, str], dict] = OrderedDict()
    for log in logs:
        if not log.order_id:
            continue
        action_label = "Printed" if log.action == "deduct-print" else "Shipped"
        key = (log.order_id, action_label)
        group = groups.setdefault(
            key,
            {"sku_groups": OrderedDict(), "timestamp": log.timestamp, "action": action_label},
        )
        sku_code = log.child_sku_code or "unknown"
        unit = log.child_unit or "pieces"
        sku_key = (sku_code, unit)
        sku_group = group["sku_groups"].setdefault(
            sku_key,
            {
                "sku_code": sku_code,
                "sku_name": log.child_sku_name or sku_code,
                "unit": unit,
                "product_lines": [],
                "total": 0.0,
            },
        )
        quantity = log.quantity or 0.0
        product_label = _format_product_label(log)
        sku_group["product_lines"].append(
            {
                "product_label": product_label,
                "quantity": quantity,
            }
        )
        sku_group["total"] += quantity

    deductions = []
    for (order_id, action_label), meta in groups.items():
        if len(deductions) >= 50:
            break
        sync_action = "print-deducted" if action_label == "Printed" else "shipping-deducted"
        sync_entry = db.scalar(
            select(SyncLog)
            .where(
                SyncLog.internal_order_id == order_id,
                SyncLog.action == sync_action,
            )
            .order_by(SyncLog.timestamp.desc())
            .limit(1)
        )
        timestamp = sync_entry.timestamp if sync_entry else meta.get("timestamp")
        if not timestamp:
            continue
        employee = get_order_employee(order_id, timestamp)
        overview = ", ".join(
            f"{sku_group['sku_name']} ({sku_group['total']:.2f})"
            for sku_group in list(meta["sku_groups"].values())[:3]
        )
        deductions.append(
            {
                "order_id": order_id,
                "timestamp": timestamp,
                "action": action_label,
                "sku_groups": list(meta["sku_groups"].values()),
                "employee": employee,
                "overview": overview or "Items deducted",
                "item_count": len(meta["sku_groups"]),
            }
        )

    return templates.TemplateResponse(
        "logs/deductions.html",
        {
            "request": request,
            "deductions": deductions,
            "order_number": order_number,
            "current_user": current_user,
        },
    )


@router.get("/deductions/export")
def export_deductions(
    days: int = Query(90, ge=1, le=3650),
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    logs = db.scalars(
        select(AuditLog)
        .where(AuditLog.action.in_(DEDUCTION_ACTIONS))
        .where(AuditLog.timestamp >= cutoff)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    ).all()
    return csv_response(
        f"deductions-last-{days}-days.csv",
        ["timestamp", "order_id", "action", "sku_code", "sku_name", "quantity", "unit", "product_code", "product_name", "product_quantity"],
        (
            [
                log.timestamp, log.order_id, log.action, log.child_sku_code, log.child_sku_name,
                round(log.quantity or 0.0, 4), log.child_unit, log.product_code, log.product_name,
                log.product_quantity,
            ]
            for log in logs
        ),
    )


@router.get("/changes")
def change_log(
    request: Request,
    q: str | None = Query(None),
    format: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    query = select(AuditLog).where(AuditLog.action.not_in(DEDUCTION_ACTIONS))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            AuditLog.child_sku_code.ilike(like)
            | AuditLog.product_code.ilike(like)
            | AuditLog.details.ilike(like)
            | AuditLog.action.ilike(like)
        )
    entries = db.scalars(
        query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(
            5000 if format == "csv" else 300
        )
    ).all()
    user_emails = {user.user_id: user.email for user in db.scalars(select(User))}
    if format == "csv":
        return csv_response(
            "change-history.csv",
            ["timestamp", "user", "action", "sku_code", "product_code", "quantity", "details"],
            (
                [
                    e.timestamp, user_emails.get(e.user_id, "System"), e.action,
                    e.child_sku_code, e.product_code, e.quantity, e.details,
                ]
                for e in entries
            ),
        )
    return templates.TemplateResponse(
        "logs/changes.html",
        {
            "request": request,
            "entries": entries,
            "user_emails": user_emails,
            "q": q or "",
            "current_user": current_user,
            "title": "Change History",
        },
    )
