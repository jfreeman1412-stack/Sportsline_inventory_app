import pytest
from fastapi import HTTPException

from backend.app.auth import ensure_manager_or_owner
from backend.app.models import RoleEnum, User


def test_ensure_manager_or_owner_raises_for_operator():
    user = User(email="op@example.com", password_hash="x", role=RoleEnum.operator)
    with pytest.raises(HTTPException):
        ensure_manager_or_owner(user)


def test_ensure_manager_or_owner_allows_manager():
    manager = User(email="mgr@example.com", password_hash="x", role=RoleEnum.manager)
    assert ensure_manager_or_owner(manager) == manager


def test_login_is_throttled_after_repeated_failures(session):
    from fastapi.testclient import TestClient

    from backend.app.auth import hash_password
    from backend.app.database import get_db
    from backend.app.main import app
    from backend.app.services import login_limiter

    login_limiter.reset()
    session.add(User(email="owner@example.com", password_hash=hash_password("right-pass"), role=RoleEnum.owner))
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    try:
        client = TestClient(app)
        for _ in range(login_limiter.MAX_FAILURES_PER_IP_AND_EMAIL):
            bad = client.post("/login", data={"email": "owner@example.com", "password": "nope"})
            assert bad.status_code == 401
        blocked = client.post(
            "/login", data={"email": "owner@example.com", "password": "right-pass"}, follow_redirects=False
        )
        assert blocked.status_code == 429
    finally:
        app.dependency_overrides.clear()
        login_limiter.reset()
