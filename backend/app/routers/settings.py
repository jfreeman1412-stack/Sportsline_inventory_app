from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import ensure_manager_or_owner, get_current_user
from ..database import get_db
from ..services.app_settings import get_app_settings

router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory="backend/app/templates")


@router.get("/settings/system")
def system_settings(
    request: Request,
    current_user=Depends(ensure_manager_or_owner),
    db: Session = Depends(get_db),
):
    settings = get_app_settings(db)
    return templates.TemplateResponse(
        "settings/system.html",
        {"request": request, "current_user": current_user, "setting": settings, "title": "System Settings"},
    )


@router.post("/settings/system")
def update_system_settings(
    request: Request,
    email_alerts_enabled: str | None = Form(None),
    smtp_host: str | None = Form(None),
    smtp_port: str | None = Form(None),
    smtp_user: str | None = Form(None),
    smtp_password: str | None = Form(None),
    smtp_from: str | None = Form(None),
    price_spike_pct: float | None = Form(None),
    low_stock_cta: str | None = Form(None),
    current_user=Depends(ensure_manager_or_owner),
    db: Session = Depends(get_db),
):
    settings_model = get_app_settings(db)
    settings_model.email_alerts_enabled = bool(email_alerts_enabled)
    settings_model.smtp_host = smtp_host.strip() if smtp_host else None
    settings_model.smtp_port = int(smtp_port) if smtp_port else None
    settings_model.smtp_user = smtp_user.strip() if smtp_user else None
    if smtp_password:
        settings_model.smtp_password = smtp_password.strip()
    settings_model.smtp_from = smtp_from.strip() if smtp_from else None
    settings_model.price_spike_pct = price_spike_pct if price_spike_pct is not None else settings_model.price_spike_pct
    settings_model.low_stock_cta = low_stock_cta.strip() if low_stock_cta else None
    db.commit()
    return RedirectResponse(url="/settings/system", status_code=302)
