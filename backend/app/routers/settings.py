from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import ensure_manager_or_owner, get_current_user
from ..database import get_db
from ..models import RoleEnum, Tag, User
from ..services.app_settings import get_app_settings

router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory="backend/app/templates")


def _get_all_tags(db: Session) -> list[Tag]:
    return db.scalars(select(Tag).order_by(Tag.name)).all()


@router.get("/settings")
def settings_page(
    request: Request,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    setting = get_app_settings(db)
    users = []
    if current_user.role in (RoleEnum.manager, RoleEnum.owner):
        users = db.scalars(select(User)).all()
    return templates.TemplateResponse(
        "settings/index.html",
        {
            "request": request,
            "current_user": current_user,
            "users": users,
            "setting": setting,
        "tags": _get_all_tags(db),
        "title": "Settings",
        },
    )


@router.get("/settings/system")
def system_settings(
    request: Request,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return RedirectResponse(url="/settings", status_code=302)


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
    deduction_window_days: int | None = Form(None),
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
    if deduction_window_days:
        settings_model.deduction_window_days = max(1, deduction_window_days)
    db.commit()
    return RedirectResponse(url="/settings", status_code=302)


@router.post("/settings/tags")
def create_tag(
    tag_name: str = Form(...),
    tag_color: str | None = Form(None),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trimmed = tag_name.strip()
    if not trimmed:
        return RedirectResponse(url="/settings", status_code=303)
    exists = db.scalar(select(Tag).where(Tag.name == trimmed))
    if not exists:
        tag = Tag(name=trimmed, color=tag_color.strip() if tag_color else None)
        db.add(tag)
        db.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/tags/{tag_id}/delete")
def delete_tag(
    tag_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tag = db.scalar(select(Tag).where(Tag.tag_id == tag_id))
    if tag:
        db.delete(tag)
        db.commit()
    return RedirectResponse(url="/settings", status_code=302)
