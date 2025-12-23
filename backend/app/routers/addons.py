from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import ensure_manager_or_owner
from ..database import get_db
from ..models import AddOnMapping, SKU

router = APIRouter(prefix="/addons", tags=["addons"])
BASE_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(BASE_TEMPLATES))


@router.get("/")
def list_addons(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    mappings = db.scalars(select(AddOnMapping)).all()
    return templates.TemplateResponse(
        "addons/list.html",
        {"request": request, "mappings": mappings, "current_user": current_user},
    )


@router.get("/create")
def create_addon_form(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    skus = db.scalars(select(SKU)).all()
    return templates.TemplateResponse(
        "addons/form.html",
        {"request": request, "skus": skus, "current_user": current_user, "mode": "Create"},
    )


@router.post("/create")
def create_addon(
    request: Request,
    add_on_name: str = Form(...),
    sku_code: str = Form(...),
    quantity: float = Form(1.0),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    mapping = AddOnMapping(
        add_on_name=add_on_name.strip(),
        sku_code=sku_code.strip(),
        quantity=quantity,
        notes=notes,
    )
    db.add(mapping)
    db.commit()
    return RedirectResponse(url="/addons", status_code=303)


@router.get("/{mapping_id}/edit")
def edit_addon_form(
    mapping_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    mapping = db.scalar(select(AddOnMapping).where(AddOnMapping.id == mapping_id))
    if not mapping:
        return RedirectResponse(url="/addons", status_code=302)
    skus = db.scalars(select(SKU)).all()
    return templates.TemplateResponse(
        "addons/form.html",
        {
            "request": request,
            "skus": skus,
            "mapping": mapping,
            "current_user": current_user,
            "mode": "Edit",
        },
    )


@router.post("/{mapping_id}/edit")
def update_addon(
    mapping_id: int,
    request: Request,
    add_on_name: str = Form(...),
    sku_code: str = Form(...),
    quantity: float = Form(1.0),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    mapping = db.scalar(select(AddOnMapping).where(AddOnMapping.id == mapping_id))
    if not mapping:
        return RedirectResponse(url="/addons", status_code=302)
    mapping.add_on_name = add_on_name.strip()
    mapping.sku_code = sku_code.strip()
    mapping.quantity = quantity
    mapping.notes = notes
    db.commit()
    return RedirectResponse(url="/addons", status_code=303)


@router.post("/{mapping_id}/delete")
def delete_addon(
    mapping_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    mapping = db.scalar(select(AddOnMapping).where(AddOnMapping.id == mapping_id))
    if mapping:
        db.delete(mapping)
        db.commit()
    return RedirectResponse(url="/addons", status_code=303)
