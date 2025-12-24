from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import ensure_manager_or_owner, get_current_user
from ..database import get_db
from ..models import PurchaseLog, SKU, SKURecipe, User
from ..notifications import notify_price_spike, notify_stock_alert

BASE_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
router = APIRouter(prefix="/skus", tags=["skus"])
templates = Jinja2Templates(directory=str(BASE_TEMPLATES))


def _get_sku_or_404(db: Session, sku_id: int) -> SKU:
    sku = db.scalar(select(SKU).where(SKU.sku_id == sku_id))
    if not sku:
        raise HTTPException(status_code=404, detail="SKU not found")
    return sku


@router.get("/")
def list_skus(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skus = db.scalars(select(SKU)).all()
    return templates.TemplateResponse(
        "skus/list.html",
        {"request": request, "skus": skus, "current_user": current_user},
    )


@router.get("/create")
def create_sku_form(
    request: Request, current_user: User = Depends(ensure_manager_or_owner)
):
    return templates.TemplateResponse(
        "skus/create.html",
        {"request": request, "current_user": current_user},
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
    return templates.TemplateResponse(
        "skus/detail.html",
        {
            "request": request,
            "sku": sku,
            "recipes": recipes,
            "purchase_logs": purchase_logs,
            "price_chart_labels": [log.purchase_date.strftime("%Y-%m-%d") for log in sorted_logs],
            "price_chart_values": [log.price for log in sorted_logs],
            "available_children": db.scalars(select(SKU).where(SKU.sku_id != sku.sku_id)).all(),
            "now": datetime.utcnow(),
            "current_user": current_user,
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
    return templates.TemplateResponse("skus/edit.html", {"request": request, "sku": sku})


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
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    sku = _get_sku_or_404(db, sku_id)
    log = PurchaseLog(
        sku_id=sku.sku_id,
        purchase_date=date.fromisoformat(purchase_date),
        quantity=quantity,
        price=price,
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
    delta_qty = quantity - log.quantity
    sku.current_stock += delta_qty
    log.purchase_date = date.fromisoformat(purchase_date)
    log.quantity = quantity
    log.price = price
    log.supplier_name = supplier_name
    log.supplier_url = supplier_url
    log.supplier_code = supplier_code
    log.notes = notes
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
