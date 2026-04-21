import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import Order, Player, Role, User
from app.orders.services import bootstrap_order_rows


@pytest.fixture()
def app(tmp_path):
    app = create_app(TestConfig)
    upload_dir = tmp_path / "uploads"
    pdf_dir = tmp_path / "pdfs"
    upload_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    app.config["UPLOAD_DIR"] = str(upload_dir)
    app.config["PDF_OUTPUT_DIR"] = str(pdf_dir)
    with app.app_context():
        db.create_all()
        admin = User(email="admin@example.com", full_name="Admin", role=Role.ADMIN.value)
        admin.set_password("Password123")
        operator = User(email="giri@gmail.com", full_name="Giri", role=Role.OPERATOR.value)
        operator.set_password("Password123")
        db.session.add(admin)
        db.session.add(operator)
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(client):
    client.post(
        "/login",
        data={"username": "admin", "password": "Password123"},
        follow_redirects=True,
    )
    return client


@pytest.fixture()
def operator_client(client):
    client.post(
        "/login",
        data={"username": "giri", "password": "Password123"},
        follow_redirects=True,
    )
    return client


@pytest.fixture()
def sample_order(app):
    with app.app_context():
        order = Order(
            order_id="202602-081-LA-LEGENDS",
            customer_name="JEEVAKA WEERASINGHE",
            mobile="+19512887962",
            shipping_address="2247 TIFFANY LANE",
            city="COLTON",
            zip_code="92324",
            state="CA",
            country="USA",
        )
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.commit()
        return order.id
