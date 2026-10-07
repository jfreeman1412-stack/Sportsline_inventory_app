from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import (
    clear_session_cookie,
    ensure_manager_or_owner,
    ensure_owner,
    get_current_user,
    get_current_user_optional,
    hash_password,
    set_session_cookie,
    verify_password,
)
from ..database import get_db
from ..models import RoleEnum, User
from ..services import login_limiter

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


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
    email = email.lower().strip()
    client_ip = request.client.host if request.client else "unknown"
    if login_limiter.is_blocked(client_ip, email):
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": "Too many failed attempts. Wait 15 minutes and try again.",
                "current_user": None,
                "title": "Login",
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(password, user.password_hash):
        login_limiter.record_failure(client_ip, email)
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
    login_limiter.record_success(client_ip, email)
    redirect = RedirectResponse(url="/", status_code=302)
    set_session_cookie(redirect, user.user_id)
    return redirect


MIN_PASSWORD_LENGTH = 8


def _registration_open(db: Session) -> bool:
    # Self sign-up only creates the first (Owner) account. After that, the Owner adds
    # people from Settings so nobody who finds the URL can create an account.
    return (db.scalar(select(func.count()).select_from(User)) or 0) == 0


def _settings_redirect(msg: str | None = None) -> RedirectResponse:
    url = "/settings" if not msg else f"/settings?{urlencode({'msg': msg})}"
    return RedirectResponse(url=url, status_code=303)


@router.get("/register")
def register_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "auth/register.html",
        {"request": request, "title": "Register", "closed": not _registration_open(db)},
    )


@router.post("/register")
def register(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not _registration_open(db):
        return templates.TemplateResponse(
            "auth/register.html",
            {"request": request, "title": "Register", "closed": True},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        return templates.TemplateResponse(
            "auth/register.html",
            {
                "request": request,
                "title": "Register",
                "error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
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
def logout():
    # The cookie must be cleared on the response actually returned to the browser.
    redirect = RedirectResponse(url="/login", status_code=302)
    clear_session_cookie(redirect)
    return redirect


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
    if user:
        user.receives_stock_alerts = bool(receives_alerts)
        db.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/users/{user_id}/role")
def update_user_role(
    user_id: int,
    role: RoleEnum = Form(...),
    current_user: User = Depends(ensure_owner),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.user_id == user_id))
    if not user:
        return RedirectResponse(url="/settings", status_code=303)
    if user.role == RoleEnum.owner and role != RoleEnum.owner:
        owners = db.scalar(
            select(func.count()).select_from(User).where(User.role == RoleEnum.owner)
        )
        if owners <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The app needs at least one Owner. Promote someone else first.",
            )
    user.role = role
    db.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/users/create")
def create_user(
    email: str = Form(...),
    password: str = Form(...),
    role: RoleEnum = Form(RoleEnum.operator),
    current_user: User = Depends(ensure_owner),
    db: Session = Depends(get_db),
):
    if len(password) < MIN_PASSWORD_LENGTH:
        return _settings_redirect(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    user = User(email=email.lower().strip(), password_hash=hash_password(password), role=role)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _settings_redirect(f"{user.email} already has an account.")
    return _settings_redirect(f"Added {user.email}. Share the temporary password with them.")


@router.post("/settings/users/{user_id}/delete")
def delete_user(
    user_id: int,
    current_user: User = Depends(ensure_owner),
    db: Session = Depends(get_db),
):
    if user_id == current_user.user_id:
        return _settings_redirect("You can't remove your own account.")
    user = db.scalar(select(User).where(User.user_id == user_id))
    if user:
        db.delete(user)
        db.commit()
        return _settings_redirect(f"Removed {user.email}.")
    return _settings_redirect()


@router.post("/account/password")
def change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(current_password, current_user.password_hash):
        return _settings_redirect("Current password is incorrect.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return _settings_redirect(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    current_user.password_hash = hash_password(new_password)
    db.commit()
    return _settings_redirect("Password updated.")
