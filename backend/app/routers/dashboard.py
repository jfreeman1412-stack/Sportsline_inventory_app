from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..legacy_db import LegacySession
from ..models import AuditLog, SKU, Tag

BASE_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(BASE_TEMPLATES))

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _get_filtered_skus(
    db: Session,
    tag_id: int | None = None,
    vendor: str | None = None,
    product_type: str | None = None,
) -> list[SKU]:
    query = select(SKU).options(selectinload(SKU.tags))
    if vendor:
        query = query.where(SKU.vendor_name == vendor)
    if tag_id:
        query = query.join(SKU.tags).where(Tag.tag_id == tag_id)
    skus = db.scalars(query).unique().all()
    return skus


def _build_filter_context(
    db: Session,
    tag_id: int | None,
    vendor: str | None,
    product_type: str | None,
) -> dict[str, Any]:
    available_tags = db.scalars(select(Tag).order_by(Tag.name)).all()
    vendor_rows = db.scalars(
        select(func.distinct(SKU.vendor_name)).where(SKU.vendor_name.is_not(None)).order_by(SKU.vendor_name)
    ).all()
    return {
        "tags": available_tags,
        "vendors": [vendor for vendor in vendor_rows if vendor],
        "selected_tag_id": tag_id,
        "selected_vendor": vendor,
        "selected_product_type": product_type or "raw",
    }


@router.get("/")
def dashboard_home(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
        },
    )


@router.get("/kpis")
def dashboard_kpis(
    request: Request,
    tag_id: int | None = None,
    vendor: str | None = None,
    product_type: str | None = None,
    db: Session = Depends(get_db),
):
    skus = _get_filtered_skus(db, tag_id, vendor, product_type)
    total_items = sum(float(sku.current_stock or 0) for sku in skus)
    low_stock_items = sum(
        1
        for sku in skus
        if sku.current_stock is not None
        and sku.current_stock < (sku.alert_threshold_qty or 10)
    )
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_deductions = (
        db.scalar(
            select(func.sum(AuditLog.quantity))
            .where(AuditLog.timestamp >= today_start)
            .where(AuditLog.action.ilike("%deduct%"))
        )
        or 0
    )
    sku_codes = [sku.sku_code for sku in skus if sku.sku_code]
    sku_map = {sku.sku_code: sku for sku in skus if sku.sku_code}

    movers_query = (
        select(AuditLog.child_sku_code, func.sum(AuditLog.quantity).label("total"))
        .where(AuditLog.child_sku_code.in_(sku_codes))
        .where(AuditLog.timestamp >= datetime.utcnow() - timedelta(days=7))
        .group_by(AuditLog.child_sku_code)
        .order_by(func.sum(AuditLog.quantity).desc())
        .limit(10)
    )
    movers = []
    if sku_codes:
        for row in db.execute(movers_query).all():
            code = row.child_sku_code
            name = sku_map.get(code).name if sku_map.get(code) else code
            movers.append({"sku": code, "name": name, "qty": float(row.total or 0)})

    context = {
        "request": request,
        "total_items": f"{total_items:,.2f}",
        "low_stock_items": low_stock_items,
        "today_deductions": f"{today_deductions:,.2f}",
        "top_movers": movers,
    }
    context.update(_build_filter_context(db, tag_id, vendor, product_type))
    return templates.TemplateResponse("dashboard/_kpis.html", context)


@router.get("/filters")
def dashboard_filters(
    request: Request,
    tag_id: int | None = None,
    vendor: str | None = None,
    product_type: str | None = None,
    db: Session = Depends(get_db),
):
    context = _build_filter_context(db, tag_id, vendor, product_type)
    context["request"] = request
    return templates.TemplateResponse("dashboard/_filters.html", context)


def _month_ranges(months: int = 12) -> list[str]:
    now = datetime.utcnow()
    base = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = base - timedelta(days=30 * (months - 1))
    labels = []
    for i in range(months):
        month = (start.month + i - 1) % 12 + 1
        year = start.year + (start.month + i - 1) // 12
        labels.append(f"{year:04d}-{month:02d}")
    return labels


@router.get("/trend-data")
def dashboard_trend_data(
    tag_id: int | None = None,
    vendor: str | None = None,
    product_type: str | None = None,
    db: Session = Depends(get_db),
):
    skus = _get_filtered_skus(db, tag_id, vendor, product_type)
    sku_codes = [sku.sku_code for sku in skus if sku.sku_code]
    labels = _month_ranges(12)
    if not sku_codes:
        return JSONResponse({"labels": labels, "datasets": []})

    sku_to_tags = {sku.sku_code: [tag.name for tag in sku.tags] for sku in skus}
    tag_totals: dict[str, list[float]] = {"All": [0.0] * len(labels)}

    start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_date = start_date - timedelta(days=30 * (len(labels) - 1))
    end_date = datetime.utcnow()

    placeholders = ", ".join([f":sku_{i}" for i in range(len(sku_codes))])
    query = f"""
        SELECT c.cart_sku AS sku,
               DATE_FORMAT(l.update_date, '%%Y-%%m') AS month_label,
               SUM(c.cart_qty) AS qty
        FROM ms_order_status_logs l
        JOIN ms_cart c ON l.order_id = c.cart_order
        WHERE l.update_date >= :start
          AND l.update_date <= :end
          AND l.order_open_status IN (39, 40)
          AND c.cart_sku IN ({placeholders})
        GROUP BY month_label, c.cart_sku
        ORDER BY month_label ASC;
    """
    params = {
        "start": start_date,
        "end": end_date,
    }
    params.update({f"sku_{i}": sku for i, sku in enumerate(sku_codes)})
    with LegacySession() as legacy:
        result = legacy.execute(text(query), params).all()

    label_index = {label: idx for idx, label in enumerate(labels)}
    for row in result:
        month = row.month_label
        if month not in label_index:
            continue
        idx = label_index[month]
        qty = float(row.qty or 0)
        tag_totals["All"][idx] += qty
        for tag_name in sku_to_tags.get(row.sku, []):
            tag_totals.setdefault(tag_name, [0.0] * len(labels))
            tag_totals[tag_name][idx] += qty

    colors = [
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
        "#9467bd",
        "#8c564b",
        "#e377c2",
        "#7f7f7f",
        "#bcbd22",
        "#17becf",
    ]
    datasets = []
    for idx, (tag, data) in enumerate(sorted(tag_totals.items())):
        datasets.append(
            {
                "label": tag,
                "data": data,
                "borderColor": colors[idx % len(colors)],
                "backgroundColor": "transparent",
                "fill": False,
                "tension": 0.2,
            }
        )

    return JSONResponse({"labels": labels, "datasets": datasets})
