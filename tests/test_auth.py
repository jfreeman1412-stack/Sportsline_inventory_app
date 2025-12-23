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
