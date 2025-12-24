from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import (
    clear_session_cookie,
    ensure_manager_or_owner,
    get_current_user,
    get_current_user_optional,
    hash_password,
    set_session_cookie,
    verify_password,
)
from ..database import get_db
from ..models import RoleEnum, User

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="backend/app/templates")


def _first_user_role(db: Session) -> RoleEnum:
    has_users = db.scalar(select(func.count()).select_from(User)) or 0
    return RoleEnum.owner if has_users == 0 else RoleEnum.operator


@router.get("/login")
def login_form(request: Request, current_user: User | None = Depends(get_current_user_optional)):
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "current_user": current_user, "title": "Login"},
    )


@router.post("/login")
def login(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == email.lower()))
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": "Invalid credentials",
                "current_user": None,
                "title": "Login",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    redirect = RedirectResponse(url="/", status_code=302)
    set_session_cookie(redirect, user.user_id)
    return redirect


@router.get("/register")
def register_form(request: Request):
    return templates.TemplateResponse(
        "auth/register.html", {"request": request, "title": "Register"}
    )


@router.post("/register")
def register(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = User(
        email=email.lower().strip(),
        password_hash=hash_password(password),
        role=_first_user_role(db),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(
            "auth/register.html",
            {
                "request": request,
                "error": "Email already registered",
                "title": "Register",
            },
            status_code=status.HTTP_409_CONFLICT,
        )
    redirect = RedirectResponse(url="/", status_code=302)
    set_session_cookie(redirect, user.user_id)
    return redirect


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return RedirectResponse(url="/login", status_code=302)


@router.get("/account")
def account(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return RedirectResponse(url="/settings", status_code=302)


@router.post("/account/alerts")
def toggle_alerts(
    request: Request,
    receives_alerts: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.receives_stock_alerts = bool(receives_alerts)
    db.commit()
    return RedirectResponse(url="/account", status_code=302)


@router.get("/settings/users")
def user_settings(
    request: Request,
    current_user: User = Depends(ensure_manager_or_owner),
    db: Session = Depends(get_db),
):
    users = db.scalars(select(User)).all()
    return templates.TemplateResponse(
        "settings/users.html",
        {"request": request, "current_user": current_user, "users": users, "title": "Settings"},
    )


@router.post("/settings/users/{user_id}/alerts")
def update_user_alert(
    user_id: int,
    receives_alerts: str | None = Form(None),
    current_user: User = Depends(ensure_manager_or_owner),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.user_id == user_id))
    if not user:
        return RedirectResponse(url="/settings/users", status_code=302)
    user.receives_stock_alerts = receives_alerts
    db.commit()
    return RedirectResponse(url="/settings/users", status_code=302)
