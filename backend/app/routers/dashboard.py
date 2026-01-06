from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from ..auth import get_current_user, get_current_user_optional
from ..config import settings
from ..database import get_db
from ..forecasting import run_nightly_forecast
from ..legacy_db import LegacySession
from ..models import AuditLog, ForecastResult, Product, ProductRecipe, ReorderAlert, SKU, Tag, User

BASE_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(BASE_TEMPLATES))
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _get_filtered_skus(
    db: Session,
    tag_id: int | None = None,
    vendor: str | None = None,
    product_type: str | None = None,
    raw_material: str | None = None,
) -> list[SKU]:
    query = select(SKU).options(selectinload(SKU.tags))
    if vendor:
        query = query.where(SKU.vendor_name == vendor)
    if tag_id:
        query = query.join(SKU.tags).where(Tag.tag_id == tag_id)
    if raw_material:
        query = query.where(SKU.sku_code == raw_material)
    skus = db.scalars(query).unique().all()
    return skus


def _parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _normalize_param(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def _get_active_product_codes(db: Session) -> list[str]:
    product_codes = [
        str(code)
        for code in db.scalars(select(Product.product_code).where(Product.is_active)).all()
        if code
    ]
    logger.debug("Active product codes count=%d sample=%s", len(product_codes), product_codes[:5])
    return product_codes


def _build_filter_context(
    db: Session,
    tag_id: int | None,
    vendor: str | None,
    product_type: str | None,
    raw_material: str | None,
) -> dict[str, Any]:
    raw_material = _normalize_param(raw_material)
    available_tags = db.scalars(select(Tag).order_by(Tag.name)).all()
    vendor_rows = db.scalars(
        select(func.distinct(SKU.vendor_name)).where(SKU.vendor_name.is_not(None)).order_by(SKU.vendor_name)
    ).all()
    child_ids = {
        row.child_sku_id
        for row in db.execute(
            select(ProductRecipe.child_sku_id).join(Product, Product.product_id == ProductRecipe.parent_product_id).where(Product.is_active)
        )
    }
    raw_materials = (
        db.scalars(select(SKU).where(SKU.sku_id.in_(child_ids)).order_by(SKU.name)).all()
        if child_ids
        else []
    )
    return {
        "tags": available_tags,
        "vendors": [vendor for vendor in vendor_rows if vendor],
        "selected_tag_id": tag_id,
        "selected_vendor": vendor,
        "selected_product_type": product_type or "raw",
        "raw_materials": raw_materials if raw_materials else db.scalars(select(SKU).order_by(SKU.name)).all(),
        "selected_raw_material": raw_material,
    }


@router.get("/")
def dashboard_home(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "app_debug": settings.app_debug,
        },
    )


@router.get("/kpis")
def dashboard_kpis(
    request: Request,
    tag_id: str | None = Query(None),
    vendor: str | None = Query(None),
    product_type: str | None = Query(None),
    raw_material: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    tag_id_val = _parse_optional_int(tag_id)
    raw_material_val = _normalize_param(raw_material)
    skus = _get_filtered_skus(db, tag_id_val, vendor, product_type, raw_material_val)
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
    alerts_count = (
        db.scalar(select(func.count()).where(ReorderAlert.active)) or 0
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
        "alerts_count": alerts_count,
        "current_user": current_user,
    }
    context.update(_build_filter_context(db, tag_id_val, vendor, product_type, raw_material_val))
    return templates.TemplateResponse("dashboard/_kpis.html", context)


@router.get("/alerts")
def dashboard_alerts(
    request: Request,
    db: Session = Depends(get_db),
    tag_id: str | None = Query(None),
    vendor: str | None = Query(None),
    product_type: str | None = Query(None),
    raw_material: str | None = Query(None),
    current_user: User | None = Depends(get_current_user_optional),
):
    tag_id_val = _parse_optional_int(tag_id)
    raw_material_val = _normalize_param(raw_material)
    skus = _get_filtered_skus(db, tag_id_val, vendor, product_type, raw_material_val)
    sku_ids = [sku.sku_id for sku in skus if sku.sku_id]
    alerts_query = (
        select(ReorderAlert)
        .where(ReorderAlert.active)
        .order_by(ReorderAlert.alert_level.desc(), ReorderAlert.generated_at.desc())
        .options(selectinload(ReorderAlert.sku))
        .limit(10)
    )
    if sku_ids:
        alerts_query = alerts_query.where(ReorderAlert.sku_id.in_(sku_ids))
    active_alerts = db.scalars(alerts_query).all()
    context = {
        "request": request,
        "alerts": [
            {
                "name": alert.sku.name if alert.sku else "Unknown",
                "sku": alert.sku.sku_code if alert.sku else "Unknown",
                "message": alert.message,
                "level": alert.alert_level,
            }
            for alert in active_alerts
        ],
        "current_user": current_user,
    }
    return templates.TemplateResponse("dashboard/_alerts.html", context)


@router.get("/filters")
def dashboard_filters(
    request: Request,
    tag_id: str | None = Query(None),
    vendor: str | None = Query(None),
    product_type: str | None = Query(None),
    raw_material: str | None = Query(None),
    db: Session = Depends(get_db),
):
    tag_id_val = _parse_optional_int(tag_id)
    raw_material_val = _normalize_param(raw_material)
    context = _build_filter_context(db, tag_id_val, vendor, product_type, raw_material_val)
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


def _future_month_labels(last_label: str, months: int = 3) -> list[str]:
    if not last_label:
        return []
    year, month = map(int, last_label.split("-"))
    labels = []
    for i in range(1, months + 1):
        future_month = ((month - 1 + i) % 12) + 1
        future_year = year + ((month - 1 + i) // 12)
        labels.append(f"{future_year:04d}-{future_month:02d}")
    return labels


TREND_COLORS = [
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


def _build_tag_totals(
    skus: list[SKU],
    product_codes: list[str],
    labels: list[str],
    start_date: datetime,
    end_date: datetime,
) -> tuple[dict[str, list[float]], list]:
    sku_codes = [sku.sku_code for sku in skus if sku.sku_code]
    if not product_codes:
        return {"All": [0.0] * len(labels)}, []
    placeholders = ", ".join([f":code_{i}" for i in range(len(product_codes))])
    product_filter = f"AND c.cart_sku IN ({placeholders})"
    query = f"""
        SELECT c.cart_sku AS sku,
               DATE_FORMAT(l.update_date, '%%Y-%%m') AS month_label,
               SUM(c.cart_qty) AS qty
        FROM ms_order_status_logs l
        JOIN ms_cart c ON l.order_id = c.cart_order
        WHERE l.update_date >= :start
          AND l.update_date <= :end
          AND l.order_open_status IN (39, 40)
          {product_filter}
        GROUP BY month_label, c.cart_sku
        ORDER BY month_label ASC;
    """
    params = {"start": start_date, "end": end_date}
    params.update({f"code_{i}": code for i, code in enumerate(product_codes)})
    with LegacySession() as legacy:
        exec_query = legacy.execute(text(query), params)
        rows = exec_query.all()
    logger.info(
        "Trend query rows=%d sku_filter=%s", len(rows), sku_codes[:5] if len(sku_codes) <= 5 else sku_codes[:5]
    )
    label_index = {label: idx for idx, label in enumerate(labels)}
    sku_to_tags = {
        sku.sku_code: [tag.name for tag in sku.tags] for sku in skus if sku.sku_code
    }
    tag_totals: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(labels))
    tag_totals["All"] = [0.0] * len(labels)
    for row in rows:
        month_label = row.month_label
        if month_label not in label_index:
            continue
        idx = label_index[month_label]
        qty = float(row.qty or 0)
        tag_totals["All"][idx] += qty
        for tag_name in sku_to_tags.get(row.sku, []):
            tag_totals[tag_name][idx] += qty
    logger.info(
        "Aggregated trend data rows=%d skus=%d total_usage=%.2f start=%s end=%s",
        len(rows),
        len(sku_codes),
        sum(tag_totals["All"]),
        start_date,
        end_date,
    )
    return dict(tag_totals), rows


def _collect_forecast_totals(db: Session, skus: list[SKU]) -> dict[str, float]:
    sku_map = {sku.sku_id: sku for sku in skus if sku.sku_id}
    if not sku_map:
        return {}
    forecast_query = (
        select(ForecastResult)
        .where(ForecastResult.sku_id.in_(sku_map.keys()))
        .order_by(ForecastResult.sku_id, ForecastResult.created_at.desc())
    )
    forecast_rows = db.scalars(forecast_query).all()
    latest_forecasts: dict[int, ForecastResult] = {}
    for forecast in forecast_rows:
        if forecast.sku_id not in latest_forecasts:
            latest_forecasts[forecast.sku_id] = forecast
    forecast_totals: dict[str, float] = defaultdict(float)
    for sku_id, forecast in latest_forecasts.items():
        if sku_id not in sku_map:
            continue
        tag_names = [tag.name for tag in sku_map[sku_id].tags] or ["All"]
        for tag_name in tag_names:
            forecast_totals[tag_name] += float(forecast.predicted_quantity or 0)
    return dict(forecast_totals)


@router.get("/trend-data")
def dashboard_trend_data(
    tag_id: str | None = Query(None),
    vendor: str | None = Query(None),
    product_type: str | None = Query(None),
    raw_material: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    tag_id_val = _parse_optional_int(tag_id)
    raw_material_val = _normalize_param(raw_material)
    skus = _get_filtered_skus(db, tag_id_val, vendor, product_type, raw_material_val)
    product_codes = _get_active_product_codes(db)
    labels = _month_ranges(24)
    start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_date = start_date - timedelta(days=30 * (len(labels) - 1))
    end_date = datetime.utcnow()
    tag_totals, rows = _build_tag_totals(skus, product_codes, labels, start_date, end_date)
    datasets = [
        {
            "label": tag,
            "data": data,
            "borderColor": TREND_COLORS[idx % len(TREND_COLORS)],
            "backgroundColor": "transparent",
            "fill": False,
            "tension": 0.2,
        }
        for idx, (tag, data) in enumerate(sorted(tag_totals.items()))
    ]
    forecast_labels = _future_month_labels(labels[-1]) if labels else []
    forecast_totals = _collect_forecast_totals(db, skus)
    forecast_datasets = []
    for idx, (tag, total) in enumerate(sorted(forecast_totals.items())):
        if not forecast_labels:
            break
        monthly_value = total / len(forecast_labels)
        forecast_datasets.append(
            {
                "label": f"{tag} Forecast",
                "data": [monthly_value] * len(forecast_labels),
                "borderColor": TREND_COLORS[idx % len(TREND_COLORS)],
                "backgroundColor": "transparent",
                "borderDash": [6, 6],
                "pointRadius": 0,
            }
        )
    return JSONResponse(
        {
            "labels": labels,
            "datasets": datasets,
            "forecast_labels": forecast_labels,
            "forecast_datasets": forecast_datasets,
        }
    )


@router.get("/admin/debug-trend-data")
def debug_trend_data(
    tag_id: str | None = Query(None),
    vendor: str | None = Query(None),
    product_type: str | None = Query(None),
    raw_material: str | None = Query(None),
    db: Session = Depends(get_db),
):
    if not settings.app_debug:
        raise HTTPException(status_code=403, detail="Debug data not available")
    tag_id_val = _parse_optional_int(tag_id)
    raw_material_val = _normalize_param(raw_material)
    skus = _get_filtered_skus(db, tag_id_val, vendor, product_type, raw_material_val)
    labels = _month_ranges(12)
    start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_date = start_date - timedelta(days=30 * (len(labels) - 1))
    end_date = datetime.utcnow()
    product_codes = _get_active_product_codes(db)
    tag_totals, rows = _build_tag_totals(skus, product_codes, labels, start_date, end_date)
    forecast_totals = _collect_forecast_totals(db, skus)
    payload = {
        "labels": labels,
        "history_rows": [
            {"sku": row.sku, "month": row.month_label, "qty": float(row.qty or 0)}
            for row in rows
        ],
        "tag_totals": tag_totals,
        "forecast_totals": forecast_totals,
    }
    return JSONResponse(payload)


@router.post("/admin/run-forecast-now")
def run_forecast_now(
    current_user: User = Depends(get_current_user),
):
    if not settings.app_debug:
        raise HTTPException(status_code=403, detail="Manual forecasts disabled")
    run_nightly_forecast()
    return JSONResponse({"status": "forecast queued"})
