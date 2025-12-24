from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppSetting


def get_app_settings(db: Session) -> AppSetting:
    setting = db.scalar(select(AppSetting).limit(1))
    if not setting:
        setting = AppSetting()
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting
