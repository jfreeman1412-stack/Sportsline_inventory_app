from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse

BASE_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(BASE_TEMPLATES))

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/")
def dashboard_home(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "current_user": None,
        },
    )


@router.get("/kpis")
def dashboard_kpis(request: Request):
    # Placeholder values; real logic will populate with aggregates.
    context = {
        "request": request,
        "total_items": "--",
        "low_stock_items": "--",
        "today_deductions": "--",
    }
    return templates.TemplateResponse("dashboard/_kpis.html", context)


@router.get("/filters")
def dashboard_filters(request: Request):
    return templates.TemplateResponse(
        "dashboard/_filters.html",
        {
            "request": request,
            "tags": [],  # fill with actual tags later
            "vendors": [],  # vendor list placeholder
        },
    )


@router.get("/trend-data")
def dashboard_trend_data():
    now = datetime.utcnow()
    months = [now.replace(day=1)]
    for i in range(1, 12):
        months.append(months[-1].replace(month=((months[-1].month - 2) % 12) + 1))
    data = {
        "labels": [m.strftime("%Y-%m") for m in reversed(months)],
        "series": [
            {"label": "Raw Material A", "values": [100 + i * 5 for i in range(12)]},
            {"label": "Raw Material B", "values": [80 + i * 3 for i in range(12)]},
        ],
    }
    return JSONResponse(data)
