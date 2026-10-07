from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import asc, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import ensure_manager_or_owner, get_current_user
from ..database import get_db
from ..models import AuditLog, PurchaseLog, RoleEnum, SKU, SKURecipe, Tag, User
from ..notifications import notify_price_spike, notify_stock_alert
from ..services.audit import DEDUCTION_ACTIONS, describe_changes, record_change, snapshot

BASE_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
router = APIRouter(prefix="/skus", tags=["skus"])
templates = Jinja2Templates(directory=str(BASE_TEMPLATES))

AVERAGE_WINDOW_DAYS = 30
SKU_AUDIT_FIELDS = (
    "name",
    "description",
    "vendor_name",
    "vendor_url",
    "salesman_name",
    "salesman_phone",
    "salesman_email",
    "unit_of_measure",
    "current_stock",
    "waste_pct",
    "alert_threshold_qty",
)
PURCHASE_AUDIT_FIELDS = (
    "purchase_date",
    "quantity",
    "price",
    "applies_to_stock",
    "supplier_name",
    "supplier_code",
)


def _is_manager(user: User) -> bool:
    return user.role in (RoleEnum.manager, RoleEnum.owner)
SORT_COLUMNS = {
    "name": SKU.name,
    "sku_code": SKU.sku_code,
    "unit": SKU.unit_of_measure,
    "vendor": SKU.vendor_name,
    "stock": SKU.current_stock,
    "threshold": SKU.alert_threshold_qty,
}


def _get_sku_or_404(db: Session, sku_id: int) -> SKU:
    sku = db.scalar(select(SKU).where(SKU.sku_id == sku_id))
    if not sku:
        raise HTTPException(status_code=404, detail="SKU not found")
    return sku


def _get_all_tags(db: Session) -> list[Tag]:
    return db.scalars(select(Tag).order_by(Tag.name)).all()


def _calculate_price_pct_changes(logs: list[PurchaseLog]) -> list[dict[str, str | int] | None]:
    pct_changes: list[dict[str, str | int] | None] = []
    prev_price: float | None = None
    for log in logs:
        if prev_price is None or prev_price == 0:
            pct_changes.append(None)
        else:
            change = ((log.price - prev_price) / prev_price) * 100
            pct_changes.append(
                {
                    "label": f"{change:+.2f}%",
                    "direction": 1 if change > 0 else -1 if change < 0 else 0,
                }
            )
        prev_price = log.price
    return pct_changes


def _average_daily_usage(db: Session) -> dict[str, float]:
    """Average deducted quantity per day over the last AVERAGE_WINDOW_DAYS, by SKU code."""
    cutoff = datetime.utcnow() - timedelta(days=AVERAGE_WINDOW_DAYS)
    rows = db.execute(
        select(AuditLog.child_sku_code, func.sum(AuditLog.quantity))
        .where(AuditLog.action.in_(["deduct-print", "deduct-shipping"]))
        .where(AuditLog.timestamp >= cutoff)
        .group_by(AuditLog.child_sku_code)
    ).all()
    return {code: (total or 0.0) / AVERAGE_WINDOW_DAYS for code, total in rows if code}


def _list_context(
    request: Request,
    db: Session,
    current_user: User,
    skus: list[SKU],
    sort_by: str,
    sort_dir: str,
    tag_id: int | None,
) -> dict:
    available_tags = _get_all_tags(db)
    return {
        "request": request,
        "current_user": current_user,
        "sort_by": sort_by if sort_by in SORT_COLUMNS else "name",
        "sort_dir": "desc" if sort_dir.lower() == "desc" else "asc",
        "available_tags": available_tags,
        "selected_tag": next((t for t in available_tags if t.tag_id == tag_id), None),
        "available_optionals": {
            sku.sku_id: [tag for tag in available_tags if tag not in sku.tags] for sku in skus
        },
        "average_usage_map": _average_daily_usage(db),
        "average_window_days": AVERAGE_WINDOW_DAYS,
    }


def _render_row(
    request: Request,
    db: Session,
    current_user: User,
    sku: SKU,
    sort_by: str,
    sort_dir: str,
    tag_id: int | None,
):
    context = _list_context(request, db, current_user, [sku], sort_by, sort_dir, tag_id)
    context["sku"] = sku
    return templates.TemplateResponse("skus/_row.html", context)


def _parse_optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Not a number: {value}")


def _sync_tags(db: Session, sku: SKU, tag_ids: list[int] | None):
    if tag_ids:
        tags = db.scalars(select(Tag).where(Tag.tag_id.in_(tag_ids))).all()
    else:
        tags = []
    sku.tags = tags


@router.get("/")
def list_skus(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    tag_id: int | None = None,
    sort_by: str = "name",
    sort_dir: str = "asc",
):
    column = SORT_COLUMNS.get(sort_by, SKU.name)
    order = desc(column) if sort_dir.lower() == "desc" else asc(column)
    query = select(SKU)
    if tag_id:
        query = query.join(SKU.tags).where(Tag.tag_id == tag_id)
    skus = db.scalars(query.order_by(order)).all()
    context = _list_context(request, db, current_user, skus, sort_by, sort_dir, tag_id)
    context["skus"] = skus
    return templates.TemplateResponse("skus/list.html", context)


@router.get("/export")
def export_skus(
    format: str = "csv",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skus = db.scalars(select(SKU).order_by(SKU.name)).all()
    if format == "markdown":
        rows = ["| SKU | Name | Stock | Threshold |", "| --- | --- | --- | --- |"]
        rows.extend(
            f"| {sku.sku_code} | {sku.name} | {sku.current_stock} | {sku.alert_threshold_qty or '-'} |"
            for sku in skus
        )
        return PlainTextResponse("\n".join(rows), media_type="text/markdown")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["sku_code", "name", "unit", "current_stock", "alert_threshold_qty", "vendor_name"]
    )
    for sku in skus:
        writer.writerow(
            [
                sku.sku_code,
                sku.name,
                sku.unit_of_measure,
                sku.current_stock,
                "" if sku.alert_threshold_qty is None else sku.alert_threshold_qty,
                sku.vendor_name or "",
            ]
        )
    return PlainTextResponse(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="raw-materials.csv"'},
    )


@router.post("/{sku_id}/quick-update")
def quick_update_sku(
    request: Request,
    sku_id: int,
    current_stock: float = Form(...),
    alert_threshold_qty: str | None = Form(None),
    sort_by: str = Form("name"),
    sort_dir: str = Form("asc"),
    tag_id: int | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    """Inline stock count / reorder amount edit from the Raw Materials table."""
    sku = _get_sku_or_404(db, sku_id)
    previous_stock = sku.current_stock
    sku.current_stock = current_stock
    sku.alert_threshold_qty = _parse_optional_float(alert_threshold_qty)
    if previous_stock != current_stock:
        record_change(
            db,
            current_user,
            "manual-adjust",
            describe_changes({"current_stock": previous_stock}, {"current_stock": current_stock}),
            sku=sku,
            quantity=current_stock - previous_stock,
        )
    db.commit()
    notify_stock_alert(db, sku, previous_stock=previous_stock)
    return _render_row(request, db, current_user, sku, sort_by, sort_dir, tag_id)


@router.post("/{sku_id}/tags/add")
def add_sku_tag(
    request: Request,
    sku_id: int,
    add_tag_id: int = Form(...),
    sort_by: str = Form("name"),
    sort_dir: str = Form("asc"),
    tag_id: int | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sku = _get_sku_or_404(db, sku_id)
    tag = db.scalar(select(Tag).where(Tag.tag_id == add_tag_id))
    if tag and tag not in sku.tags:
        sku.tags.append(tag)
        db.commit()
    return _render_row(request, db, current_user, sku, sort_by, sort_dir, tag_id)


@router.post("/{sku_id}/tags/{remove_tag_id}/remove")
def remove_sku_tag(
    request: Request,
    sku_id: int,
    remove_tag_id: int,
    sort_by: str = Form("name"),
    sort_dir: str = Form("asc"),
    tag_id: int | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sku = _get_sku_or_404(db, sku_id)
    sku.tags = [tag for tag in sku.tags if tag.tag_id != remove_tag_id]
    db.commit()
    return _render_row(request, db, current_user, sku, sort_by, sort_dir, tag_id)


@router.get("/create")
def create_sku_form(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        "skus/create.html",
        {
            "request": request,
            "current_user": current_user,
            "available_tags": _get_all_tags(db),
        },
    )


@router.post("/create")
def create_sku(
    request: Request,
    name: str = Form(...),
    sku_code: str = Form(...),
    description: str | None = Form(None),
    vendor_name: str | None = Form(None),
    vendor_url: str | None = Form(None),
    salesman_name: str | None = Form(None),
    salesman_phone: str | None = Form(None),
    salesman_email: str | None = Form(None),
    unit_of_measure: str = Form("pieces"),
    current_stock: float = Form(0.0),
    waste_pct: float = Form(0.0),
    alert_threshold_qty: float | None = Form(None),
    tag_ids: list[int] | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not _is_manager(current_user):
        # Operators add basic SKUs; waste % and thresholds are Manager settings.
        waste_pct, alert_threshold_qty = 0.0, None
    sku = SKU(
        name=name.strip(),
        sku_code=sku_code.strip(),
        description=description,
        unit_of_measure=unit_of_measure,
        current_stock=current_stock,
        waste_pct=waste_pct,
        alert_threshold_qty=alert_threshold_qty,
        vendor_name=vendor_name.strip() if vendor_name else None,
        vendor_url=vendor_url.strip() if vendor_url else None,
        salesman_name=salesman_name.strip() if salesman_name else None,
        salesman_phone=salesman_phone.strip() if salesman_phone else None,
        salesman_email=salesman_email.strip() if salesman_email else None,
    )
    _sync_tags(db, sku, tag_ids)
    db.add(sku)
    record_change(
        db,
        current_user,
        "sku-create",
        f"Created {sku.sku_code} with stock {sku.current_stock}",
        sku=sku,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=422, detail="SKU code already exists")
    notify_stock_alert(db, sku)
    return RedirectResponse(url=f"/skus/{sku.sku_id}", status_code=303)


@router.get("/{sku_id}")
def sku_detail(
    request: Request,
    sku_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sku = _get_sku_or_404(db, sku_id)
    recipes = db.scalars(select(SKURecipe).where(SKURecipe.parent_sku_id == sku.sku_id)).all()
    purchase_logs = (
        db.scalars(
            select(PurchaseLog)
            .where(PurchaseLog.sku_id == sku.sku_id)
            .order_by(PurchaseLog.purchase_date.desc())
        )
        .all()
    )
    sorted_logs = list(reversed(purchase_logs))
    last_purchase = purchase_logs[0] if purchase_logs else None
    history = db.scalars(
        select(AuditLog)
        .where(AuditLog.child_sku_code == sku.sku_code)
        .where(AuditLog.action.not_in(DEDUCTION_ACTIONS))
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(25)
    ).all()
    user_emails = {
        user.user_id: user.email
        for user in db.scalars(
            select(User).where(User.user_id.in_({h.user_id for h in history if h.user_id}))
        )
    }
    return templates.TemplateResponse(
        "skus/detail.html",
        {
            "request": request,
            "sku": sku,
            "recipes": recipes,
            "purchase_logs": purchase_logs,
            "price_chart_labels": [log.purchase_date.strftime("%Y-%m-%d") for log in sorted_logs],
            "price_chart_values": [log.price for log in sorted_logs],
            "price_chart_pct": _calculate_price_pct_changes(sorted_logs),
            "available_children": db.scalars(select(SKU).where(SKU.sku_id != sku.sku_id)).all(),
            "now": datetime.utcnow(),
            "current_user": current_user,
            "last_purchase": last_purchase,
            "history": history,
            "user_emails": user_emails,
            "available_tags": _get_all_tags(db),
        },
    )


@router.get("/{sku_id}/edit")
def edit_sku_form(
    request: Request,
    sku_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sku = _get_sku_or_404(db, sku_id)
    return templates.TemplateResponse(
        "skus/edit.html",
        {
            "request": request,
            "sku": sku,
            "available_tags": _get_all_tags(db),
            "current_user": current_user,
        },
    )


@router.post("/{sku_id}/edit")
def edit_sku(
    request: Request,
    sku_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    vendor_name: str | None = Form(None),
    vendor_url: str | None = Form(None),
    salesman_name: str | None = Form(None),
    salesman_phone: str | None = Form(None),
    salesman_email: str | None = Form(None),
    unit_of_measure: str = Form("pieces"),
    current_stock: str | None = Form(None),
    waste_pct: str | None = Form(None),
    alert_threshold_qty: str | None = Form(None),
    tag_ids: list[int] | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sku = _get_sku_or_404(db, sku_id)
    before = snapshot(sku, SKU_AUDIT_FIELDS)
    previous_stock = sku.current_stock
    sku.name = name.strip()
    sku.description = description
    sku.unit_of_measure = unit_of_measure
    if _is_manager(current_user):
        # Stock overrides, waste % and thresholds are Manager/Owner only.
        new_stock = _parse_optional_float(current_stock)
        if new_stock is not None:
            sku.current_stock = new_stock
        sku.waste_pct = _parse_optional_float(waste_pct) or 0.0
        sku.alert_threshold_qty = _parse_optional_float(alert_threshold_qty)
    _sync_tags(db, sku, tag_ids)
    sku.vendor_name = vendor_name.strip() if vendor_name else None
    sku.vendor_url = vendor_url.strip() if vendor_url else None
    sku.salesman_name = salesman_name.strip() if salesman_name else None
    sku.salesman_phone = salesman_phone.strip() if salesman_phone else None
    sku.salesman_email = salesman_email.strip() if salesman_email else None
    changes = describe_changes(before, snapshot(sku, SKU_AUDIT_FIELDS))
    if changes:
        stock_delta = sku.current_stock - previous_stock
        record_change(
            db, current_user, "sku-edit", changes, sku=sku, quantity=stock_delta or None
        )
    db.commit()
    notify_stock_alert(db, sku, previous_stock=previous_stock)
    return RedirectResponse(url=f"/skus/{sku.sku_id}", status_code=303)


@router.post("/{sku_id}/delete")
def delete_sku(
    request: Request,
    sku_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    sku = _get_sku_or_404(db, sku_id)
    record_change(db, current_user, "sku-delete", f"Deleted {sku.sku_code} ({sku.name})", sku=sku)
    db.delete(sku)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "This raw material is still used in a BOM or has purchase history. "
                "Remove it from those first."
            ),
        )
    return RedirectResponse(url="/skus", status_code=303)


@router.post("/{sku_id}/bom")
def add_bom_item(
    request: Request,
    sku_id: int,
    child_sku_id: int = Form(...),
    qty_used: float = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    sku = _get_sku_or_404(db, sku_id)
    child = _get_sku_or_404(db, child_sku_id)
    if child.sku_id == sku.sku_id:
        raise HTTPException(status_code=400, detail="Cannot add SKU as its own child")
    recipe = SKURecipe(
        parent_sku_id=sku.sku_id, child_sku_id=child.sku_id, qty_used=qty_used
    )
    db.add(recipe)
    record_change(
        db, current_user, "bom-add", f"Added {qty_used} × {child.sku_code} to BOM", sku=sku
    )
    db.commit()
    return RedirectResponse(url=f"/skus/{sku.sku_id}", status_code=303)


@router.post("/{sku_id}/bom/{recipe_id}/delete")
def remove_bom_item(
    request: Request,
    sku_id: int,
    recipe_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    recipe = db.scalar(select(SKURecipe).where(SKURecipe.id == recipe_id))
    if recipe and recipe.parent_sku_id == sku_id:
        record_change(
            db,
            current_user,
            "bom-remove",
            f"Removed {recipe.child.sku_code} from BOM",
            sku=recipe.parent,
        )
        db.delete(recipe)
        db.commit()
    return RedirectResponse(url=f"/skus/{sku_id}", status_code=303)


@router.post("/{sku_id}/purchase-log")
def add_purchase_log(
    request: Request,
    sku_id: int,
    purchase_date: str = Form(...),
    quantity: float = Form(...),
    price: float = Form(...),
    supplier_name: str | None = Form(None),
    supplier_url: str | None = Form(None),
    supplier_code: str | None = Form(None),
    notes: str | None = Form(None),
    add_to_stock: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    sku = _get_sku_or_404(db, sku_id)
    applies_to_stock = add_to_stock is not None
    log = PurchaseLog(
        sku_id=sku.sku_id,
        purchase_date=date.fromisoformat(purchase_date),
        quantity=quantity,
        price=price,
        applies_to_stock=applies_to_stock,
        supplier_name=supplier_name,
        supplier_url=supplier_url,
        supplier_code=supplier_code,
        notes=notes,
    )
    previous_log = (
        db.scalars(
            select(PurchaseLog)
            .where(PurchaseLog.sku_id == sku.sku_id)
            .order_by(PurchaseLog.purchase_date.desc())
            .limit(1)
        )
        .all()
    )
    previous_price = previous_log[0].price if previous_log else None
    if applies_to_stock:
        sku.current_stock += quantity
    db.add(log)
    record_change(
        db,
        current_user,
        "purchase-add",
        f"Logged purchase of {quantity} at {price} on {purchase_date}"
        + (" (added to stock)" if applies_to_stock else ""),
        sku=sku,
        quantity=quantity if applies_to_stock else None,
    )
    db.commit()
    notify_stock_alert(db, sku)
    if previous_price:
        notify_price_spike(db, sku, previous_price, price)
    return RedirectResponse(url=f"/skus/{sku.sku_id}", status_code=303)


@router.get("/{sku_id}/purchase-log/{log_id}/edit")
def edit_purchase_log_form(
    request: Request,
    sku_id: int,
    log_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    sku = _get_sku_or_404(db, sku_id)
    log = db.scalar(select(PurchaseLog).where(PurchaseLog.id == log_id))
    if not log or log.sku_id != sku.sku_id:
        raise HTTPException(status_code=404, detail="Purchase log not found")
    return templates.TemplateResponse(
        "skus/purchase_edit.html",
        {"request": request, "sku": sku, "log": log, "current_user": current_user},
    )


@router.post("/{sku_id}/purchase-log/{log_id}/edit")
def edit_purchase_log(
    request: Request,
    sku_id: int,
    log_id: int,
    purchase_date: str = Form(...),
    quantity: float = Form(...),
    price: float = Form(...),
    supplier_name: str | None = Form(None),
    supplier_url: str | None = Form(None),
    supplier_code: str | None = Form(None),
    notes: str | None = Form(None),
    add_to_stock: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    sku = _get_sku_or_404(db, sku_id)
    log = db.scalar(select(PurchaseLog).where(PurchaseLog.id == log_id))
    if not log or log.sku_id != sku.sku_id:
        raise HTTPException(status_code=404, detail="Purchase log not found")
    previous_logs = (
        db.scalars(
            select(PurchaseLog)
            .where(PurchaseLog.sku_id == sku.sku_id, PurchaseLog.id != log.id)
            .order_by(PurchaseLog.purchase_date.desc())
            .limit(1)
        )
        .all()
    )
    previous_price = previous_logs[0].price if previous_logs else None
    before = snapshot(log, PURCHASE_AUDIT_FIELDS)
    stock_before = sku.current_stock
    if log.applies_to_stock:
        sku.current_stock -= log.quantity
    log.purchase_date = date.fromisoformat(purchase_date)
    log.quantity = quantity
    log.price = price
    log.applies_to_stock = add_to_stock is not None
    log.supplier_name = supplier_name
    log.supplier_url = supplier_url
    log.supplier_code = supplier_code
    log.notes = notes
    if log.applies_to_stock:
        sku.current_stock += quantity
    changes = describe_changes(before, snapshot(log, PURCHASE_AUDIT_FIELDS))
    if changes:
        record_change(
            db,
            current_user,
            "purchase-edit",
            f"Edited purchase #{log.id}: {changes}",
            sku=sku,
            quantity=(sku.current_stock - stock_before) or None,
        )
    db.commit()
    notify_stock_alert(db, sku)
    if previous_price:
        notify_price_spike(db, sku, previous_price, price)
    return RedirectResponse(url=f"/skus/{sku.sku_id}", status_code=303)
