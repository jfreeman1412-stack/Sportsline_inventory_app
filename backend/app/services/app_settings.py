from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from ..models import AppSetting


def _ensure_deduction_column(db: Session) -> None:
    db.execute(
        text(
            "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS deduction_window_days INT NOT NULL DEFAULT 30"
        )
    )
    db.commit()


def get_app_settings(db: Session) -> AppSetting:
    try:
        setting = db.scalar(select(AppSetting).limit(1))
    except ProgrammingError as exc:
        if "deduction_window_days" in str(exc):
            _ensure_deduction_column(db)
            setting = db.scalar(select(AppSetting).limit(1))
        else:
            raise

    if not setting:
        setting = AppSetting()
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting
