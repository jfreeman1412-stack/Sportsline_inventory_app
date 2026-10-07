import os

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
os.environ.setdefault("APP_MYSQL_HOST", "localhost")
os.environ.setdefault("APP_MYSQL_USER", "test")
os.environ.setdefault("APP_MYSQL_PASSWORD", "test")
os.environ.setdefault("APP_MYSQL_DATABASE", "test")
os.environ.setdefault("SMTP_HOST", "smtp.example.com")
os.environ.setdefault("SMTP_PORT", "587")
os.environ.setdefault("SMTP_USER", "alerts@example.com")
os.environ.setdefault("SMTP_PASSWORD", "secret")
os.environ.setdefault("INTERNAL_SYNC_TOKEN", "token")
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base


@pytest.fixture(autouse=True)
def clear_login_throttle():
    from backend.app.services import login_limiter

    login_limiter.reset()


@pytest.fixture(autouse=True)
def no_legacy_db(monkeypatch):
    """Tests never talk to the legacy MySQL orders database."""
    monkeypatch.setattr("backend.app.routers.sync.get_legacy_product_name", lambda sku: None)
    monkeypatch.setattr("backend.app.routers.logs.get_order_employee", lambda *a: None)


@pytest.fixture(scope="function")
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=True, future=True)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
