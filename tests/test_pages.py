"""Smoke tests: every page renders for a logged-in owner against an empty-ish database."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import AuditLog, Product, PurchaseLog, SKU, ShippingMapping, Tag, AddOnMapping


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def owner(client, session):
    response = client.post(
        "/register", data={"email": "Owner@Example.com", "password": "pw"}, follow_redirects=False
    )
    assert response.status_code == 302
    return client


@pytest.fixture
def seeded(session):
    sku = SKU(name="Roll, 8in", sku_code="ROLL8", current_stock=50.0, alert_threshold_qty=20.0)
    tag = Tag(name="Paper", color="#336699")
    session.add_all([sku, tag, Product(product_code="8X10", name="8x10")])
    session.add(ShippingMapping(length=12, width=9, height=0.25, items_json='["ROLL8"]'))
    session.add(AddOnMapping(add_on_name="Gloss", sku_code="ROLL8", quantity=1))
    session.commit()
    session.add(PurchaseLog(sku_id=sku.sku_id, purchase_date=date(2026, 1, 1), quantity=5, price=10))
    session.add(AuditLog(order_id=1, action="deduct-print", child_sku_code="ROLL8", quantity=3))
    session.commit()
    return sku, tag


def test_logged_out_browser_is_sent_to_login(client):
    for path in ["/", "/skus/"]:
        response = client.get(path, headers={"accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/skus/",
        "/skus/?sort_by=stock&sort_dir=desc",
        "/skus/1",
        "/skus/1/edit",
        "/skus/create",
        "/skus/1/purchase-log/1/edit",
        "/products/",
        "/products/1",
        "/products/1/edit",
        "/products/create",
        "/shipping/mappings",
        "/shipping/mappings/create",
        "/shipping/mappings/1/edit",
        "/addons/",
        "/addons/create",
        "/addons/1/edit",
        "/logs/deductions",
        "/settings",
        "/login",
        "/register",
    ],
)
def test_page_renders(owner, seeded, path):
    response = owner.get(path)
    assert response.status_code == 200, response.text[:500]


def test_export_csv_quotes_commas(owner, seeded):
    response = owner.get("/skus/export?format=csv")
    assert response.status_code == 200
    assert '"Roll, 8in"' in response.text


def test_quick_update_and_tags(owner, seeded, session):
    sku, tag = seeded
    response = owner.post(
        f"/skus/{sku.sku_id}/quick-update",
        data={"current_stock": "42", "alert_threshold_qty": "", "sort_by": "name", "sort_dir": "asc"},
    )
    assert response.status_code == 200
    assert 'id="sku-row-' in response.text
    session.refresh(sku)
    assert sku.current_stock == 42 and sku.alert_threshold_qty is None

    response = owner.post(f"/skus/{sku.sku_id}/tags/add", data={"add_tag_id": tag.tag_id})
    assert response.status_code == 200 and "Paper" in response.text
    response = owner.post(f"/skus/{sku.sku_id}/tags/{tag.tag_id}/remove")
    assert response.status_code == 200
    session.refresh(sku)
    assert sku.tags == []


def test_owner_can_change_roles_but_not_remove_last_owner(owner, session):
    from backend.app.models import RoleEnum, User

    other = User(email="op@example.com", password_hash="x")
    session.add(other)
    session.commit()
    owner.post(f"/settings/users/{other.user_id}/role", data={"role": "manager"})
    session.refresh(other)
    assert other.role == RoleEnum.manager
    me = session.query(User).filter_by(email="owner@example.com").one()
    response = owner.post(f"/settings/users/{me.user_id}/role", data={"role": "operator"})
    assert response.status_code == 400


def test_logout_clears_session(owner):
    response = owner.post("/logout", follow_redirects=False)
    assert response.status_code == 302
    assert "inventory_session=" in response.headers.get("set-cookie", "")
    assert owner.get("/settings", follow_redirects=False).status_code in (401, 303)
