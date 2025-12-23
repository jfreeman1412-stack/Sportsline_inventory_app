import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import ensure_manager_or_owner, get_current_user
from ..database import get_db
from ..models import SKU, ShippingMapping

router = APIRouter(prefix="/shipping", tags=["shipping"])
templates = Jinja2Templates(directory="backend/app/templates")


@router.get("/mappings")
def list_mappings(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    mappings = db.scalars(select(ShippingMapping)).all()
    return templates.TemplateResponse(
        "shipping/list.html",
        {"request": request, "mappings": mappings, "current_user": current_user},
    )


@router.get("/mappings/create")
def create_mapping_form(request: Request, current_user=Depends(ensure_manager_or_owner), db: Session = Depends(get_db)):
    skus = db.scalars(select(SKU)).all()
    return templates.TemplateResponse(
        "shipping/form.html",
        {"request": request, "skus": skus, "current_user": current_user, "mode": "Create"},
    )


@router.post("/mappings/create")
def create_mapping(
    request: Request,
    dimensions_string: str | None = Form(None),
    length: float | None = Form(None),
    width: float | None = Form(None),
    height: float | None = Form(None),
    package_code: str | None = Form(None),
    items: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    mapping = ShippingMapping(
        dimensions_string=dimensions_string.strip() if dimensions_string and dimensions_string.strip() else None,
        length=length,
        width=width,
        height=height,
        package_code=package_code.strip() if package_code else None,
        items_json=json.dumps(items),
    )
    db.add(mapping)
    db.commit()
    return RedirectResponse(url="/shipping/mappings", status_code=303)


@router.get("/mappings/{mapping_id}/edit")
def edit_mapping_form(
    mapping_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    mapping = db.scalar(select(ShippingMapping).where(ShippingMapping.id == mapping_id))
    if not mapping:
        return RedirectResponse(url="/shipping/mappings", status_code=302)
    skus = db.scalars(select(SKU)).all()
    items = set(json.loads(mapping.items_json or "[]"))
    return templates.TemplateResponse(
        "shipping/form.html",
        {
            "request": request,
            "mapping": mapping,
            "skus": skus,
            "selected_items": items,
            "current_user": current_user,
            "mode": "Edit",
        },
    )


@router.post("/mappings/{mapping_id}/edit")
def update_mapping(
    mapping_id: int,
    request: Request,
    dimensions_string: str | None = Form(None),
    length: float | None = Form(None),
    width: float | None = Form(None),
    height: float | None = Form(None),
    package_code: str | None = Form(None),
    items: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    mapping = db.scalar(select(ShippingMapping).where(ShippingMapping.id == mapping_id))
    if not mapping:
        return RedirectResponse(url="/shipping/mappings", status_code=302)
    mapping.dimensions_string = (
        dimensions_string.strip() if dimensions_string and dimensions_string.strip() else None
    )
    mapping.length = length
    mapping.width = width
    mapping.height = height
    mapping.package_code = package_code.strip() if package_code else None
    mapping.items_json = json.dumps(items)
    db.commit()
    return RedirectResponse(url="/shipping/mappings", status_code=303)


@router.post("/mappings/{mapping_id}/delete")
def delete_mapping(
    mapping_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(ensure_manager_or_owner),
):
    mapping = db.scalar(select(ShippingMapping).where(ShippingMapping.id == mapping_id))
    if mapping:
        db.delete(mapping)
        db.commit()
    return RedirectResponse(url="/shipping/mappings", status_code=303)
