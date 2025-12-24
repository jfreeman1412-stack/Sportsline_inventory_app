from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .auth import get_current_user_optional
from .config import settings
from .database import get_db
from .models import RoleEnum, SKU, SyncLog
from .routers import addons, auth, logs, products, shipstation, shipping, skus, sync, settings as settings_router

app = FastAPI(title=settings.app_name)
app.state.settings = settings

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)


@app.get("/")
def home(
    request: Request,
    db=Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    low_stock = db.scalars(
        select(SKU)
        .where(SKU.alert_threshold_qty.is_not(None))
        .where(SKU.current_stock < SKU.alert_threshold_qty)
        .order_by(SKU.current_stock.asc())
    ).all()
    recent_syncs = db.scalars(
        select(SyncLog).order_by(SyncLog.timestamp.desc()).limit(5)
    ).all()
    total_skus = db.scalar(select(func.count()).select_from(SKU)) or 0
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "low_stock": low_stock,
            "recent_syncs": recent_syncs,
            "total_skus": total_skus,
            "low_stock_count": len(low_stock),
            "current_user": current_user,
            "can_force_sync": bool(
                current_user and current_user.role in (RoleEnum.manager, RoleEnum.owner)
            ),
        },
    )


app.include_router(sync.router)
app.include_router(shipstation.router)
app.include_router(shipping.router)
app.include_router(addons.router)
app.include_router(logs.router)
app.include_router(auth.router)
app.include_router(skus.router)
app.include_router(products.router)
app.include_router(settings_router.router)
