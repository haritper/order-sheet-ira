import re

from app.extensions import db
from app.models import Role, User
from app.pricing_integration import db as pricing_db


def _extract_pricing_csrf(html: bytes) -> str:
    text = html.decode("utf-8")
    match = re.search(r'name="pricing_csrf_token"\s+value="([^"]+)"', text)
    assert match is not None
    return str(match.group(1))


def _session_pricing_csrf(client) -> str:
    with client.session_transaction() as session:
        return str(session.get("pricing_csrf_token", ""))


def _pricing_login(client, username: str, password: str):
    page = client.get("/pricing/login")
    csrf = _extract_pricing_csrf(page.data)
    return client.post(
        "/pricing/login",
        data={"username": username, "password": password, "pricing_csrf_token": csrf},
        follow_redirects=False,
    )


def test_pricing_manager_login_uses_order_creation_credentials_and_can_update_values(
    app, client, monkeypatch, tmp_path
):
    app.config["PRICING_DATABASE"] = str((tmp_path / "pricing_auth_test.db").resolve())
    with app.app_context():
        pricing_db.init_db()
        manager = User(email="manager", full_name="Manager User", role=Role.MANAGER.value)
        manager.set_password("Password123")
        db.session.add(manager)
        db.session.commit()

    called = {"count": 0}
    monkeypatch.setattr("app.pricing_integration.routes.update_pricing_override", lambda *_: called.__setitem__("count", 1))

    login_resp = _pricing_login(client, "manager", "Password123")
    assert login_resp.status_code == 302
    assert "/pricing/" in login_resp.headers.get("Location", "")

    csrf = _session_pricing_csrf(client)
    assert csrf
    post_resp = client.post(
        "/pricing/pricing",
        data={
            "pricing_csrf_token": csrf,
            "rule_id": "1",
            "override_unit_rate_inr": "123.45",
        },
        follow_redirects=True,
    )
    assert post_resp.status_code == 200
    assert called["count"] == 1


def test_pricing_owner_cannot_update_pricing_values(app, client, monkeypatch, tmp_path):
    app.config["PRICING_DATABASE"] = str((tmp_path / "pricing_owner_test.db").resolve())
    with app.app_context():
        pricing_db.init_db()

    called = {"count": 0}
    monkeypatch.setattr("app.pricing_integration.routes.update_pricing_override", lambda *_: called.__setitem__("count", 1))

    login_resp = _pricing_login(client, "admin", "ChangeMe123!")
    assert login_resp.status_code == 302
    assert "/pricing/" in login_resp.headers.get("Location", "")

    csrf = _session_pricing_csrf(client)
    assert csrf
    post_resp = client.post(
        "/pricing/pricing",
        data={
            "pricing_csrf_token": csrf,
            "rule_id": "1",
            "override_unit_rate_inr": "123.45",
        },
        follow_redirects=True,
    )
    assert post_resp.status_code == 200
    assert b"Only manager can update pricing rules." in post_resp.data
    assert called["count"] == 0
