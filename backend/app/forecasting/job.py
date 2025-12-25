from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import SKU


def run_nightly_forecast(db: Session | None = None) -> None:
    """
    Nightly forecast outline:

    1. Query legacy website DB for historical orders within the configured window.
    2. Explode each order via the BOM/recipe tables to raw material usage.
    3. Aggregate daily or monthly usage series per SKU.
    4. Fit a lightweight sklearn model (e.g., LinearRegression) with time features.
    5. Generate a 30-day forecast for each material.
    6. Persist ForecastResult rows and synthesize ReorderAlert records if stock is low.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # Placeholder operations: once real logic is ready, replace with queries/modeling.
        print(f"[{datetime.utcnow().isoformat()}] Forecast job stub running.")
        _ = db.scalars(select(SKU).limit(1)).all()  # noqa: F841
    finally:
        if close_db:
            db.close()
