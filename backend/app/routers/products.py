from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import ensure_manager_or_owner, get_current_user
from ..database import get_db
from ..models import Product, ProductRecipe, SKU, User

BASE_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
router = APIRouter(prefix="/products", tags=["products"])
templates = Jinja2Templates(directory=str(BASE_TEMPLATES))


def _get_product_or_404(db: Session, product_id: int) -> Product:
    product = db.scalar(select(Product).where(Product.product_id == product_id))
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.get("/")
def list_products(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    products = db.scalars(select(Product).order_by(Product.name.asc())).all()
    return templates.TemplateResponse(
        "products/list.html",
        {"request": request, "products": products, "current_user": current_user},
    )


@router.get("/create")
def create_product_form(
    request: Request, current_user: User = Depends(ensure_manager_or_owner)
):
    return templates.TemplateResponse(
        "products/create.html",
        {"request": request, "current_user": current_user},
    )


@router.post("/create")
def create_product(
    request: Request,
    product_code: str = Form(...),
    name: str = Form(...),
    description: str | None = Form(None),
    price: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    product = Product(
        product_code=product_code.strip(),
        name=name.strip(),
        description=description,
        price=float(price) if price not in (None, "") else None,
        is_active=bool(is_active),
    )
    db.add(product)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=422, detail="Product code already exists")
    return RedirectResponse(url=f"/products/{product.product_id}", status_code=303)


@router.get("/{product_id}")
def product_detail(
    request: Request,
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = _get_product_or_404(db, product_id)
    recipes = (
        db.scalars(
            select(ProductRecipe).where(ProductRecipe.parent_product_id == product.product_id)
        )
        .all()
    )
    return templates.TemplateResponse(
        "products/detail.html",
        {
            "request": request,
            "product": product,
            "recipes": recipes,
            "available_children": db.scalars(select(SKU)).all(),
            "current_user": current_user,
        },
    )


@router.get("/{product_id}/edit")
def edit_product_form(
    request: Request,
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    product = _get_product_or_404(db, product_id)
    return templates.TemplateResponse(
        "products/edit.html",
        {"request": request, "product": product, "current_user": current_user},
    )


@router.post("/{product_id}/edit")
def edit_product(
    request: Request,
    product_id: int,
    product_code: str = Form(...),
    name: str = Form(...),
    description: str | None = Form(None),
    price: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    product = _get_product_or_404(db, product_id)
    product.product_code = product_code.strip()
    product.name = name.strip()
    product.description = description
    product.price = float(price) if price not in (None, "") else None
    product.is_active = bool(is_active)
    db.commit()
    return RedirectResponse(url=f"/products/{product.product_id}", status_code=303)


@router.post("/{product_id}/delete")
def delete_product(
    request: Request,
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    product = _get_product_or_404(db, product_id)
    db.delete(product)
    db.commit()
    return RedirectResponse(url="/products", status_code=303)


@router.post("/{product_id}/bom")
def add_product_bom(
    request: Request,
    product_id: int,
    child_sku_id: int = Form(...),
    qty_used: float = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    product = _get_product_or_404(db, product_id)
    child = db.scalar(select(SKU).where(SKU.sku_id == child_sku_id))
    if not child:
        raise HTTPException(status_code=404, detail="Raw material not found")
    recipe = ProductRecipe(
        parent_product_id=product.product_id,
        child_sku_id=child.sku_id,
        qty_used=qty_used,
    )
    db.add(recipe)
    db.commit()
    return RedirectResponse(url=f"/products/{product.product_id}", status_code=303)


@router.post("/{product_id}/bom/{recipe_id}/delete")
def remove_product_bom(
    request: Request,
    product_id: int,
    recipe_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(ensure_manager_or_owner),
):
    recipe = db.scalar(select(ProductRecipe).where(ProductRecipe.id == recipe_id))
    if recipe:
        db.delete(recipe)
        db.commit()
    return RedirectResponse(url=f"/products/{product_id}", status_code=303)
