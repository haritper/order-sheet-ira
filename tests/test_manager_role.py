from app.extensions import db
from app.models import Order, Role, User
from app.orders.services import bootstrap_order_rows


def _login(client, username: str, password: str = "Password123"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_manager_cannot_access_admin_users_page(app, client):
    with app.app_context():
        manager = User(email="manager", full_name="Manager User", role=Role.MANAGER.value)
        manager.set_password("Password123")
        db.session.add(manager)
        db.session.commit()

    _login(client, "manager")
    resp = client.get("/admin/users")
    assert resp.status_code == 403


def test_manager_can_delete_order_with_pin(app, client):
    with app.app_context():
        manager = User(email="manager", full_name="Manager User", role=Role.MANAGER.value)
        manager.set_password("Password123")
        order = Order(order_id="ORDER-MANAGER-DEL", customer_name="Delete Target")
        bootstrap_order_rows(order)
        db.session.add(manager)
        db.session.add(order)
        db.session.commit()
        order_id = int(order.id)

    _login(client, "manager")
    resp = client.post(
        f"/orders/{order_id}/delete",
        data={"delete_pin": "2019"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"deleted" in resp.data.lower()

    with app.app_context():
        assert Order.query.get(order_id) is None


def test_manager_can_access_assign_and_assign_orders(app, client):
    with app.app_context():
        manager = User(email="manager", full_name="Manager User", role=Role.MANAGER.value)
        manager.set_password("Password123")
        db.session.add(manager)
        db.session.commit()

    _login(client, "manager")
    resp_assign = client.get("/assign")
    assert resp_assign.status_code == 200
    resp_order_assignments = client.get("/admin/order-assignments")
    assert resp_order_assignments.status_code == 200


def test_operator_cannot_access_assign_or_assign_orders(operator_client):
    resp_assign = operator_client.get("/assign")
    assert resp_assign.status_code == 403
    resp_order_assignments = operator_client.get("/admin/order-assignments")
    assert resp_order_assignments.status_code == 403


def test_admin_users_enforces_single_manager_and_single_admin(auth_client):
    first_manager = auth_client.post(
        "/admin/users",
        data={
            "full_name": "Manager One",
            "username": "manager1",
            "password": "Password123",
            "role": "manager",
        },
        follow_redirects=True,
    )
    assert first_manager.status_code == 200
    assert b"User created." in first_manager.data

    second_manager = auth_client.post(
        "/admin/users",
        data={
            "full_name": "Manager Two",
            "username": "manager2",
            "password": "Password123",
            "role": "manager",
        },
        follow_redirects=True,
    )
    assert second_manager.status_code == 200
    assert b"Only one manager user is allowed." in second_manager.data

    second_admin = auth_client.post(
        "/admin/users",
        data={
            "full_name": "Admin Two",
            "username": "admin2",
            "password": "Password123",
            "role": "admin",
        },
        follow_redirects=True,
    )
    assert second_admin.status_code == 200
    assert b"Only one admin user is allowed." in second_admin.data
