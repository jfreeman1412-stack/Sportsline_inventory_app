from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import asc, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from urllib.parse import urlencode

from ..auth import ensure_manager_or_owner, get_current_user
from ..database import get_db
from ..models import AuditLog, PurchaseLog, SKU, SKURecipe, Tag, User
from ..notifications import notify_price_spike, notify_stock_alert
from ..services.app_settings import get_app_settings

BASE_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skus", tags=["skus"])
templates = Jinja2Templates(directory=str(BASE_TEMPLATES))


def _get_sku_or_404(db: Session, sku_id: int) -> SKU:
    sku = db.scalar(select(SKU).where(SKU.sku_id == sku_id))
    if not sku:
        raise HTTPException(status_code=404, detail="SKU not found")
    return sku


def _get_all_tags(db: Session) -> list[Tag]:
    return db.scalars(select(Tag).order_by(Tag.name)).all()


def _build_order_clause(sort_by: str, sort_dir: str):
    column_map = {
        "name": SKU.name,
        "sku_code": SKU.sku_code,
        "unit": SKU.unit_of_measure,
        "vendor": SKU.vendor_name,
        "stock": SKU.current_stock,
        "threshold": SKU.alert_threshold_qty,
    }
    column = column_map.get(sort_by, SKU.name)
    direction = "desc" if sort_dir.lower() == "desc" else "asc"
    order = desc(column) if direction == "desc" else asc(column)
    return order, direction


def _average_daily_usage(db: Session, sku_codes: list[str], window_days: int) -> dict[str, float]:
    if not sku_codes:
        return {}
    window_days = max(window_days, 1)
    interval_start = datetime.utcnow() - timedelta(days=window_days)
    stmt = (
        select(AuditLog.child_sku_code, func.sum(AuditLog.quantity))
        .where(
            AuditLog.child_sku_code.in_(sku_codes),
            AuditLog.timestamp >= interval_start,
        )
        .group_by(AuditLog.child_sku_code)
    )
    rows = db.execute(stmt).all()
    return {code: total / window_days for code, total in rows if total is not None}


def _get_available_tags_for_sku(db: Session, sku: SKU, all_tags: list[Tag] | None = None) -> list[Tag]:
    tags = all_tags if all_tags is not None else _get_all_tags(db)
    assigned_ids = {tag.tag_id for tag in sku.tags}
    return [tag for tag in tags if tag.tag_id not in assigned_ids]


def _render_tags_column(
    request: Request, sku: SKU, addable_tags: list[Tag], current_user: User | None = None
):
    return templates.TemplateResponse(
        "skus/_tags_column.html",
        {
            "request": request,
            "sku": sku,
            "addable_tags": addable_tags,
            "current_user": current_user,
        },
    )


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
    order, direction = _build_order_clause(sort_by, sort_dir)
    available_tags = _get_all_tags(db)
    available_map = {}
    query = select(SKU)
    if tag_id:
        query = query.join(SKU.tags).where(Tag.tag_id == tag_id)
    skus = db.scalars(query.order_by(order)).all()
    for sku in skus:
        assigned_ids = {tag.tag_id for tag in sku.tags}
        available_map[sku.sku_id] = [tag for tag in available_tags if tag.tag_id not in assigned_ids]
    window_days = get_app_settings(db).deduction_window_days or 30
    sku_codes = [sku.sku_code for sku in skus]
    average_usage_map = _average_daily_usage(db, sku_codes, window_days)
    return templates.TemplateResponse(
        "skus/list.html",
        {
            "request": request,
            "skus": skus,
            "current_user": current_user,
            "sort_by": sort_by,
            "sort_dir": direction,
            "available_tags": available_tags,
            "selected_tag": next((t for t in available_tags if t.tag_id == tag_id), None),
            "available_optionals": available_map,
            "average_usage_map": average_usage_map,
            "average_window_days": window_days,
        },
    )


@router.post("/{sku_id}/quick-update")
def quick_update_sku_row(
    request: Request,
    sku_id: int,
    current_stock: float = Form(...),
    alert_threshold_qty: str | None = Form(None),
    sort_by: str = Form("name"),
    sort_dir: str = Form("asc"),
    tag_id: int | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sku = _get_sku_or_404(db, sku_id)
    sku.current_stock = current_stock
    sku.alert_threshold_qty = float(alert_threshold_qty) if alert_threshold_qty not in (None, "") else None
    db.commit()
    notify_stock_alert(db, sku)
    all_tags = _get_all_tags(db)
    addable = _get_available_tags_for_sku(db, sku, all_tags)
    available_optionals = {sku.sku_id: addable}
    selected_tag = db.scalar(select(Tag).where(Tag.tag_id == tag_id)) if tag_id else None
    return templates.TemplateResponse(
        "skus/_row.html",
        {
            "request": request,
            "sku": sku,
            "current_user": current_user,
            "available_optionals": available_optionals,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "selected_tag": selected_tag,
        },
    )


@router.get("/{sku_id}/tags/popover")
def tags_popover(
    request: Request,
    sku_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    logger.info("tags_popover hit for sku %s", sku_id)
    sku = _get_sku_or_404(db, sku_id)
    addable = _get_available_tags_for_sku(db, sku)
    return templates.TemplateResponse(
        "skus/_tags_popover.html",
        {"request": request, "sku": sku, "addable_tags": addable},
    )


@router.post("/{sku_id}/tags/{tag_id}/add")
def add_tag_to_sku(
    request: Request,
    sku_id: int,
    tag_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sku = _get_sku_or_404(db, sku_id)
    tag = db.scalar(select(Tag).where(Tag.tag_id == tag_id))
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    if tag not in sku.tags:
        sku.tags.append(tag)
        db.commit()
        logger.info("tag %s added to sku %s by %s", tag.name, sku_id, current_user.email)
    db.refresh(sku)
    addable = _get_available_tags_for_sku(db, sku)
    return _render_tags_column(request, sku, addable, current_user)


@router.post("/{sku_id}/tags/{tag_id}/remove")
def remove_tag_from_sku(
    request: Request,
    sku_id: int,
    tag_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sku = _get_sku_or_404(db, sku_id)
    tag = db.scalar(select(Tag).where(Tag.tag_id == tag_id))
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    if tag in sku.tags:
        sku.tags.remove(tag)
        db.commit()
        logger.info("tag %s removed from sku %s by %s", tag.name, sku_id, current_user.email)
    db.refresh(sku)
    addable = _get_available_tags_for_sku(db, sku)
    return _render_tags_column(request, sku, addable, current_user)


@router.get("/create")
def create_sku_form(
    request: Request,
    current_user: User = Depends(ensure_manager_or_owner),
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
    current_user: User = Depends(ensure_manager_or_owner),
):
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
    sort_by: str = "name",
    sort_dir: str = "asc",
    tag_id: int | None = None,
):
    sku = _get_sku_or_404(db, sku_id)
    order_clause, canonical_dir = _build_order_clause(sort_by, sort_dir)
    query = select(SKU.sku_id)
    if tag_id:
        query = query.join(SKU.tags).where(Tag.tag_id == tag_id)
    sorted_ids = db.scalars(query.order_by(order_clause)).all()
    try:
        idx = sorted_ids.index(sku.sku_id)
    except ValueError:
        idx = 0
        sorted_ids.insert(idx, sku.sku_id)
    prev_sku_id = sorted_ids[idx - 1] if idx > 0 else None
    next_sku_id = sorted_ids[idx + 1] if idx < len(sorted_ids) - 1 else None
    settings_model = get_app_settings(db)
    window_days = settings_model.deduction_window_days or 30
    avg_daily_usage = _average_daily_usage(db, [sku.sku_code], window_days).get(sku.sku_code)
    params = dict(request.query_params)
    params["sort_by"] = sort_by
    params["sort_dir"] = canonical_dir
    if tag_id:
        params["tag_id"] = tag_id
    else:
        params.pop("tag_id", None)
    query_fragment = f"?{urlencode(params)}" if params else ""
    list_url = f"/skus{query_fragment}"
    detail_prev_url = f"/skus/{prev_sku_id}{query_fragment}" if prev_sku_id else None
    detail_next_url = f"/skus/{next_sku_id}{query_fragment}" if next_sku_id else None
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
            "available_tags": _get_all_tags(db),
            "nav_prev_url": detail_prev_url,
            "nav_next_url": detail_next_url,
            "list_url": list_url,
            "avg_daily_usage": avg_daily_usage,
            "average_window_days": window_days,
        },
    )


@router.get("/{sku_id}/edit")
def edit_sku_form(
    request: Request,
    sku_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    sku = _get_sku_or_404(db, sku_id)
    return templates.TemplateResponse(
        "skus/edit.html",
        {
            "request": request,
            "sku": sku,
            "available_tags": _get_all_tags(db),
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
    current_stock: float = Form(0.0),
    waste_pct: float = Form(0.0),
    alert_threshold_qty: float | None = Form(None),
    tag_ids: list[int] | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    sku = _get_sku_or_404(db, sku_id)
    sku.name = name.strip()
    sku.description = description
    sku.unit_of_measure = unit_of_measure
    sku.current_stock = current_stock
    sku.waste_pct = waste_pct
    sku.alert_threshold_qty = alert_threshold_qty
    _sync_tags(db, sku, tag_ids)
    sku.vendor_name = vendor_name.strip() if vendor_name else None
    sku.vendor_url = vendor_url.strip() if vendor_url else None
    sku.salesman_name = salesman_name.strip() if salesman_name else None
    sku.salesman_phone = salesman_phone.strip() if salesman_phone else None
    sku.salesman_email = salesman_email.strip() if salesman_email else None
    db.commit()
    notify_stock_alert(db, sku)
    return RedirectResponse(url=f"/skus/{sku.sku_id}", status_code=303)


@router.post("/{sku_id}/delete")
def delete_sku(
    request: Request,
    sku_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    sku = _get_sku_or_404(db, sku_id)
    db.delete(sku)
    db.commit()
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
    if recipe:
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
    db.commit()
    notify_stock_alert(db, sku)
    if previous_price and price > previous_price * 1.1:
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
    db.commit()
    notify_stock_alert(db, sku)
    if previous_price and price > previous_price * 1.1:
        notify_price_spike(db, sku, previous_price, price)
    return RedirectResponse(url=f"/skus/{sku.sku_id}", status_code=303)


@router.get("/export")
def export_skus(
    format: str = "csv",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skus = db.scalars(select(SKU)).all()
    if format == "markdown":
        rows = ["| SKU | Name | Stock | Threshold |", "| --- | --- | --- | --- |"]
        rows.extend(
            f"| {sku.sku_code} | {sku.name} | {sku.current_stock} | {sku.alert_threshold_qty or '-'} |"
            for sku in skus
        )
        return PlainTextResponse("\n".join(rows), media_type="text/markdown")
    body = "sku_code,name,current_stock,alert_threshold_qty\n"
    body += "\n".join(
        f"{sku.sku_code},{sku.name},{sku.current_stock},{sku.alert_threshold_qty or ''}"
        for sku in skus
    )
    return PlainTextResponse(body, media_type="text/csv")
