from __future__ import annotations

from datetime import datetime, timedelta
import logging

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..legacy_db import LegacySession
from ..models import ForecastResult, Product, ProductRecipe, ReorderAlert, SKU

logger = logging.getLogger(__name__)


def _get_product_bom(db: Session) -> dict[str, list[tuple[str, float]]]:
    stmt = (
        select(Product.product_code, SKU.sku_code, ProductRecipe.qty_used)
        .join(ProductRecipe, ProductRecipe.parent_product_id == Product.product_id)
        .join(SKU, SKU.sku_id == ProductRecipe.child_sku_id)
    )
    result = db.execute(stmt).all()
    bom: dict[str, list[tuple[str, float]]] = {}
    for row in result:
        product_code = row.product_code
        child_code = row.sku_code
        qty_used = float(row.qty_used or 0)
        bom.setdefault(product_code, []).append((child_code, qty_used))
    return bom


def _season_flags(month: int) -> tuple[int, int]:
    return (1 if month in (3, 4, 5) else 0, 1 if month in (8, 9, 10) else 0)


def _feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("date")
    base_year = df["date"].dt.year.min()
    df["day_of_year"] = df["date"].dt.dayofyear
    df["month"] = df["date"].dt.month
    df["year"] = df["date"].dt.year
    df["sin_month"] = np.sin(2 * np.pi * df["month"] / 12)
    df["cos_month"] = np.cos(2 * np.pi * df["month"] / 12)
    df["year_trend"] = df["year"] - base_year
    df["is_spring"], df["is_fall"] = zip(*df["month"].apply(_season_flags))
    features = df[
        [
            "day_of_year",
            "sin_month",
            "cos_month",
            "year_trend",
            "is_spring",
            "is_fall",
        ]
    ]
    return features


def _future_feature_matrix(last_date: pd.Timestamp, periods: int = 30) -> pd.DataFrame:
    future_dates = pd.date_range(
        start=last_date + pd.Timedelta(days=1), periods=periods, freq="D"
    )
    df = pd.DataFrame({"date": future_dates})
    df["day_of_year"] = df["date"].dt.dayofyear
    df["month"] = df["date"].dt.month
    df["year"] = df["date"].dt.year
    df["sin_month"] = np.sin(2 * np.pi * df["month"] / 12)
    df["cos_month"] = np.cos(2 * np.pi * df["month"] / 12)
    df["year_trend"] = df["year"] - df["year"].min()
    df["is_spring"], df["is_fall"] = zip(*df["month"].apply(_season_flags))
    return df[
        [
            "day_of_year",
            "sin_month",
            "cos_month",
            "year_trend",
            "is_spring",
            "is_fall",
        ]
    ], future_dates


def _aggregate_usage(rows: list[dict], bom_map: dict[str, list[tuple[str, float]]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["sku_code", "date", "qty"])

    records: list[dict] = []
    for row in rows:
        sku = row.get("cart_sku")
        qty = float(row.get("cart_qty") or 0)
        when = row.get("update_date")
        if not when or not sku:
            continue
        when = when.date()
        if sku in bom_map:
            for child_code, child_qty in bom_map[sku]:
                records.append(
                    {
                        "sku_code": child_code,
                        "date": when,
                        "qty": qty * child_qty,
                    }
                )
        else:
            records.append({"sku_code": sku, "date": when, "qty": qty})

    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = (
        df.groupby(["sku_code", "date"], as_index=False)
        .agg({"qty": "sum"})
        .sort_values("date")
    )
    return df


def _fetch_legacy_rows(legacy: LegacySession, start: datetime, end: datetime) -> list[dict]:
    query = """
        SELECT c.cart_sku AS cart_sku,
               c.cart_qty AS cart_qty,
               l.update_date AS update_date
        FROM ms_order_status_logs l
        JOIN ms_cart c ON l.order_id = c.cart_order
        WHERE l.order_open_status IN (39, 40)
          AND l.update_date >= :start
          AND l.update_date <= :end
    """
    result = legacy.execute(text(query), {"start": start, "end": end})
    return [dict(row) for row in result.mappings()]


def run_nightly_forecast(
    db: Session | None = None,
    legacy_session: LegacySession | None = None,
    months_back: int = 24,
) -> None:
    close_db = False
    close_legacy = False
    if db is None:
        db = SessionLocal()
        close_db = True
    if legacy_session is None:
        legacy_session = LegacySession()
        close_legacy = True

    try:
        start = datetime.utcnow() - timedelta(days=months_back * 30)
        end = datetime.utcnow()
        rows = _fetch_legacy_rows(legacy_session, start, end)
        logger.info("Fetched %d legacy order rows (%s - %s)", len(rows), start, end)
        bom_map = _get_product_bom(db)
        usage_df = _aggregate_usage(rows, bom_map)
        logger.info(
            "Aggregated usage rows=%d skus=%d",
            len(usage_df),
            usage_df["sku_code"].nunique() if not usage_df.empty else 0,
        )
        if not usage_df.empty:
            logger.debug("Usage sample: %s", usage_df.head(3).to_dict("records"))
        if usage_df.empty:
            logger.info("No historical usage data found – skipping forecast.")
            return

        usage_df = usage_df[usage_df["date"] >= (datetime.utcnow() - timedelta(days=months_back * 30))]
        sku_counts = usage_df.groupby("sku_code")["qty"].sum().sort_values(ascending=False)
        top_skus = sku_counts.head(50).index.tolist()
        forecasts: list[dict] = []
        for sku_code in top_skus:
            sku_entry = db.scalar(select(SKU).where(SKU.sku_code == sku_code))
            if not sku_entry:
                continue
            sku_data = usage_df[usage_df["sku_code"] == sku_code]
            if len(sku_data) < 30:
                continue
            features = _feature_matrix(sku_data[["date", "qty"]].rename(columns={"qty": "target"}))
            X = features
            y = sku_data["qty"].values
            model = LinearRegression()
            model.fit(X, y)
            score = float(model.score(X, y))
            future_X, future_dates = _future_feature_matrix(sku_data["date"].max())
            preds = model.predict(future_X)
            predicted_30 = float(max(preds.sum(), 0))
            if predicted_30 == 0:
                continue
            forecasts.append(
                {
                    "sku": sku_entry,
                    "predicted": predicted_30,
                    "score": score,
                    "daily_prediction": preds,
                    "dates": future_dates,
                }
            )
        db.execute(delete(ForecastResult))
        db.execute(delete(ReorderAlert))
        db.flush()
        for forecast in forecasts:
            sku_entry = forecast["sku"]
            result = ForecastResult(
                sku_id=sku_entry.sku_id,
                forecast_date=datetime.utcnow().date(),
                predicted_quantity=forecast["predicted"],
                model_score=forecast["score"],
                model_version="LinearRegression-v1",
            )
            db.add(result)
            consumption_per_day = forecast["predicted"] / 30.0
            if consumption_per_day <= 0:
                continue
            current_stock = float(sku_entry.current_stock or 0)
            days_left = current_stock / consumption_per_day if consumption_per_day > 0 else float("inf")
            level = None
            if days_left < 14:
                level = "critical"
            elif days_left < 21:
                level = "warning"
            else:
                continue
            month_ids = {d.month for d in forecast["dates"]}
            season_hint = "spring" if any(m in (3, 4, 5) for m in month_ids) else "fall" if any(m in (8, 9, 10) for m in month_ids) else "upcoming season"
            message = (
                f"{sku_entry.name} – {level.capitalize()}: Only {days_left:.1f} days left ({season_hint} spike incoming)"
            )
            alert = ReorderAlert(
                sku_id=sku_entry.sku_id,
                alert_level=level,
                message=message,
                active=True,
            )
            db.add(alert)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Forecast job failed.")
        raise
    finally:
        if close_db:
            db.close()
        if close_legacy:
            legacy_session.close()


if __name__ == "__main__":
    run_nightly_forecast()
