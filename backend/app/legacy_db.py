from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import settings

LEGACY_DATABASE_URL = (
    f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
    f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}"
)

legacy_engine = create_engine(LEGACY_DATABASE_URL, future=True, pool_pre_ping=True)
LegacySession = sessionmaker(bind=legacy_engine, autoflush=False, autocommit=False, future=True)
