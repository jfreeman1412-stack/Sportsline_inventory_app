from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

SQLALCHEMY_DATABASE_URL = (
    f"mysql+pymysql://{settings.app_mysql_user}:{settings.app_mysql_password}"
    f"@{settings.app_mysql_host}:{settings.app_mysql_port}/{settings.app_mysql_database}"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_db():
    with SessionLocal() as session:
        yield session
