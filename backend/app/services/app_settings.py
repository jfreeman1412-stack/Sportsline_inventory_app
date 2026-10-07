from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppSetting


def get_app_settings(db: Session) -> AppSetting:
    setting = db.scalar(select(AppSetting).limit(1))
    if not setting:
        setting = AppSetting()
        db.add(setting)
        # Flush rather than commit: this is called mid-transaction (e.g. during a sync)
        # and must not commit the caller's half-finished work.
        db.flush()
    return setting
