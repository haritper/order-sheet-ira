import io

from app.extensions import db
from app.models import Attachment, Order, OrderAssignment, OrderAssignmentStatus, OrderStatus, Player, User
from app.orders.check_state import get_or_create_order_check, set_parsed_json
from app.orders.checklist_cutting import load_checklist_state, save_checklist_state
from app.orders.services import bootstrap_order_rows


def test_login_required_redirect(client):
    resp = client.get("/orders")
    assert resp.status_code == 302


def test_login_accepts_username_identifier(client):
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "Password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/orders" in resp.headers.get("Location", "")


def test_admin_cannot_create_order(auth_client):
    resp = auth_client.get("/orders/new")
    assert resp.status_code == 403


def test_csv_import_flow(app, auth_client):
    with app.app_context():
        order = Order(order_id="ORDER-CSV", customer_name="CSV Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.commit()
        oid = order.id

    payload = {
        "csv_file": (
            io.BytesIO(
                b"player_name,number,sleeve_type,tshirt_size,tshirt_qty,trouser_size,trouser_qty,row_number\n"
                b"Alice,9,HALF,M,2,L,2,1\n"
            ),
            "players.csv",
        )
    }
    resp = auth_client.post(
        f"/orders/{oid}/players/import-csv",
        data=payload,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Player.query.filter_by(order_id=oid).count() == 1


def test_approval_requires_ready(app, auth_client):
    with app.app_context():
        order = Order(order_id="ORDER-APPROVE", customer_name="Approve Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.commit()
        oid = order.id

    resp = auth_client.post(f"/orders/{oid}/approve", follow_redirects=True)
    assert resp.status_code == 200


def test_export_pdf_endpoint(app, auth_client, monkeypatch):
    with app.app_context():
        order = Order(order_id="ORDER-PDF", customer_name="PDF Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.commit()
        oid = order.id

    monkeypatch.setattr("app.exports.routes.render_order_pdf", lambda order: b"%PDF-1.4\n%%EOF")

    resp = auth_client.get(f"/orders/{oid}/export/pdf")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"


def test_ai_import_flow(app, auth_client, monkeypatch):
    with app.app_context():
        order = Order(order_id="ORDER-AI", customer_name="AI Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.commit()
        oid = order.id

    monkeypatch.setattr(
        "app.players.routes.parse_players_ai",
        lambda f: (
            [
                {
                    "row_number": 1,
                    "player_name": "AI Player",
                    "number": "7",
                    "sleeve_type": "HALF",
                    "tshirt_size": "M",
                    "tshirt_qty": 1,
                    "trouser_size": "M",
                    "trouser_qty": 1,
                }
            ],
            [],
        ),
    )

    payload = {"ai_file": (io.BytesIO(b"messy,data"), "roster.csv")}
    resp = auth_client.post(
        f"/orders/{oid}/players/import-ai",
        data=payload,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Player.query.filter_by(order_id=oid).count() == 1


def test_ai_import_replaces_with_valid_rows_even_when_some_errors(app, auth_client, monkeypatch):
    with app.app_context():
        order = Order(order_id="ORDER-AI-PARTIAL", customer_name="AI Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        db.session.add(
            Player(
                order_id=order.id,
                row_number=1,
                player_name="OLD",
                number="99",
                sleeve_type="HALF",
                tshirt_size="M",
                tshirt_qty=1,
                trouser_size="M",
                trouser_qty=1,
            )
        )
        db.session.commit()
        oid = order.id

    monkeypatch.setattr(
        "app.players.routes.parse_players_ai",
        lambda f: (
            [
                {
                    "row_number": 1,
                    "player_name": "NEW",
                    "number": "7",
                    "sleeve_type": "FULL",
                    "tshirt_size": "L",
                    "tshirt_qty": 1,
                    "trouser_size": "L",
                    "trouser_qty": 1,
                }
            ],
            [{"row": 2, "error": "trouser_size is invalid"}],
        ),
    )

    payload = {"ai_file": (io.BytesIO(b"messy,data"), "roster.csv")}
    resp = auth_client.post(
        f"/orders/{oid}/players/import-ai",
        data=payload,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        rows = Player.query.filter_by(order_id=oid).order_by(Player.row_number.asc()).all()
        assert len(rows) == 1
        assert rows[0].player_name == "NEW"


def test_admin_assignment_generation(auth_client, app):
    with app.app_context():
        operator = User.query.filter_by(email="giri").first()
        assert operator is not None
        data = {f"count_{operator.id}": "10"}
        for idx in range(10):
            data[f"team_name_{idx}"] = f"TEAM {idx + 1}"

    counters_resp = auth_client.post(
        "/admin/order-id-counters",
        data={"pod_next_number": "2", "ira_next_number": "1", "invoice_next_number": "1"},
        follow_redirects=True,
    )
    assert counters_resp.status_code == 200

    resp = auth_client.post("/admin/order-assignments", data=data, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        rows = OrderAssignment.query.order_by(OrderAssignment.sequence_number.asc()).all()
        assert len(rows) == 10
        assert rows[0].order_code.endswith("-TEAM-1")
        assert rows[0].sequence_number == 2
        assert rows[-1].sequence_number == 11


def test_operator_step1_dropdown_and_completion(app, operator_client):
    with app.app_context():
        operator = User.query.filter_by(email="giri").first()
        assignment = OrderAssignment(
            order_code="POD-2026-APR-002-TEST-TEAM",
            team_name="TEST TEAM",
            operator_id=operator.id,
            sequence_number=2,
            month_abbr="APR",
            year=2026,
            status=OrderAssignmentStatus.PENDING.value,
        )
        db.session.add(assignment)
        db.session.commit()
        assignment_id = assignment.id

    create_resp = operator_client.post(
        "/orders/new",
        data={
            "customer_name": "Operator Customer",
            "mobile": "+100000000",
        },
        follow_redirects=False,
    )
    assert create_resp.status_code == 302
    assert "/edit?step=1" in create_resp.headers.get("Location", "")

    edit_url = create_resp.headers["Location"]
    get_resp = operator_client.get(edit_url, follow_redirects=True)
    assert get_resp.status_code == 200
    assert b"Assigned Order ID" in get_resp.data

    order_id = int(edit_url.split("/orders/")[1].split("/edit")[0])
    post_resp = operator_client.post(
        f"/orders/{order_id}/edit?step=1",
        data={
            "step": "1",
            "assigned_order_id": str(assignment_id),
            "order_id": "",
            "customer_name": "Operator Customer",
            "mobile": "+100000000",
        },
        follow_redirects=True,
    )
    assert post_resp.status_code == 200

    with app.app_context():
        order = Order.query.get(order_id)
        assignment = OrderAssignment.query.get(assignment_id)
        assert order.assignment_id == assignment_id
        assert order.order_id == assignment.order_code
        assert assignment.status == OrderAssignmentStatus.IN_PROGRESS.value

    checklist_resp = operator_client.get(f"/orders/{order_id}/checklist", follow_redirects=True)
    assert checklist_resp.status_code == 200

    with app.app_context():
        assignment = OrderAssignment.query.get(assignment_id)
        assert assignment.status == OrderAssignmentStatus.COMPLETED.value


def test_production_plan_requires_invoice_receipt_when_toggle_enabled(app, auth_client):
    with app.app_context():
        order = Order(order_id="POD-2026-APR-099-VERIFY", customer_name="Verify Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.commit()
        oid = order.id

        save_checklist_state(
            order,
            {
                "flow": {
                    "customer_plan_generated": True,
                    "customer_plan_attachment_id": None,
                    "customer_approved": True,
                    "invoice_receipt_uploaded": False,
                    "invoice_receipt_attachment_id": None,
                    "invoice_receipt_filename": "",
                    "shipping_address": "Addr",
                    "city": "City",
                    "state": "State",
                    "zip_code": "12345",
                    "country": "USA",
                    "production_plan_generated": False,
                    "production_plan_attachment_id": None,
                }
            },
        )

    app.config["INVOICE_RECEIPT_REQUIRED"] = True

    resp = auth_client.post(
        f"/orders/{oid}/checklist",
        data={
            "current_page": "2",
            "generate_production_plan": "1",
            "shipping_address": "Addr",
            "city": "City",
            "state": "State",
            "zip_code": "12345",
            "country": "USA",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        refreshed = Order.query.get(oid)
        state = load_checklist_state(refreshed)
        flow = state.get("flow", {}) if isinstance(state, dict) else {}
        assert bool(flow.get("production_plan_generated", False)) is False


def test_invoice_receipt_skip_is_blocked_when_required(app, auth_client):
    with app.app_context():
        order = Order(order_id="POD-2026-APR-100-RECEIPT", customer_name="Receipt Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.commit()
        oid = order.id

        save_checklist_state(
            order,
            {
                "flow": {
                    "customer_plan_generated": True,
                    "customer_plan_attachment_id": None,
                    "customer_approved": True,
                    "invoice_receipt_uploaded": False,
                    "invoice_receipt_attachment_id": None,
                    "invoice_receipt_filename": "",
                    "shipping_address": "Addr",
                    "city": "City",
                    "state": "State",
                    "zip_code": "12345",
                    "country": "USA",
                    "production_plan_generated": False,
                    "production_plan_attachment_id": None,
                }
            },
        )

    app.config["INVOICE_RECEIPT_REQUIRED"] = True

    resp = auth_client.post(
        f"/orders/{oid}/checklist",
        data={
            "current_page": "2",
            "set_invoice_receipt": "1",
            "skip_invoice_receipt": "1",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invoice receipt is mandatory" in resp.data

    with app.app_context():
        refreshed = Order.query.get(oid)
        state = load_checklist_state(refreshed)
        flow = state.get("flow", {}) if isinstance(state, dict) else {}
        assert bool(flow.get("invoice_receipt_uploaded", False)) is False


def test_operator_cannot_access_other_operator_order(app, operator_client):
    with app.app_context():
        owner = User.query.filter_by(email="giri").first()
        other = User(email="subash", full_name="Subash", role="operator")
        other.set_password("Password123")
        db.session.add(other)
        db.session.flush()
        order = Order(order_id="POD-2026-APR-050-OTHER", customer_name="Other")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        assignment = OrderAssignment(
            order_code=order.order_id,
            team_name="OTHER",
            operator_id=other.id,
            sequence_number=50,
            month_abbr="APR",
            year=2026,
            status=OrderAssignmentStatus.IN_PROGRESS.value,
            linked_order_id=order.id,
        )
        db.session.add(assignment)
        db.session.flush()
        order.assignment_id = assignment.id
        db.session.commit()
        assert owner.id != other.id
        oid = order.id

    resp = operator_client.get(f"/orders/{oid}")
    assert resp.status_code == 403


def test_admin_can_delete_order_with_assignment(app, auth_client):
    with app.app_context():
        operator = User.query.filter_by(email="giri").first()
        order = Order(order_id="POD-2026-APR-060-DELETE", customer_name="Delete Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        assignment = OrderAssignment(
            order_code=order.order_id,
            team_name="DELETE",
            operator_id=operator.id,
            sequence_number=60,
            month_abbr="APR",
            year=2026,
            status=OrderAssignmentStatus.IN_PROGRESS.value,
            linked_order_id=order.id,
        )
        db.session.add(assignment)
        db.session.flush()
        order.assignment_id = assignment.id
        db.session.commit()
        oid = order.id
        assignment_id = assignment.id

    resp = auth_client.post(
        f"/orders/{oid}/delete",
        data={"delete_pin": "2019"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"deleted" in resp.data.lower()

    with app.app_context():
        assert Order.query.get(oid) is None
        assignment = OrderAssignment.query.get(assignment_id)
        assert assignment is not None
        assert assignment.linked_order_id is None
        assert assignment.status == OrderAssignmentStatus.PENDING.value


def _seed_customer_plan_ready_order(app, mobile=""):
    with app.app_context():
        order = Order(order_id="POD-2026-APR-CP-001", customer_name="Customer Plan User", mobile=mobile)
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        check = get_or_create_order_check(order)
        set_parsed_json(
            check,
            {
                "quantity_comparison": {
                    "mens_half_sleeve": {
                        "M": {"overview": 1, "packing": 1, "status": "Match"}
                    }
                }
            },
        )
        db.session.commit()
        return int(order.id)


def _customer_plan_generate_payload():
    return {
        "current_page": "2",
        "generate_customer_plan": "1",
        "order_id_verified": "on",
        "enquiry_date_verified": "on",
        "design_checked": "on",
        "logos_checked": "on",
        "gender_checked": "on",
        "sleeve_type_checked": "on",
        "names_numbers_sizes_checked": "on",
        "quantity_checked": "on",
    }


def test_customer_plan_generate_triggers_webhook_and_saves_status(app, auth_client, monkeypatch):
    app.config["AI_VERIFY_ENABLED"] = False
    oid = _seed_customer_plan_ready_order(app, mobile="+919876543210")
    monkeypatch.setattr("app.orders.routes._rebuild_garment_quantity_comparison", lambda order, parsed: None)

    called = {"count": 0}

    def _fake_webhook(**kwargs):
        called["count"] += 1
        assert kwargs["customer_mobile"] == "+919876543210"
        return {"success": True, "status_code": 200, "message": "ok", "error": None}

    monkeypatch.setattr("app.orders.routes.send_customer_plan_webhook", _fake_webhook)

    resp = auth_client.post(
        f"/orders/{oid}/checklist",
        data=_customer_plan_generate_payload(),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert called["count"] == 1

    with app.app_context():
        order = Order.query.get(oid)
        state = load_checklist_state(order)
        flow = state.get("flow", {}) if isinstance(state, dict) else {}
        assert bool(flow.get("customer_plan_generated", False)) is True
        assert str(flow.get("customer_plan_last_send_status", "")) == "success"
        assert int(flow.get("customer_plan_last_sent_attachment_id", 0) or 0) > 0


def test_customer_plan_generate_missing_mobile_opens_modal_then_generates(
    app, auth_client, monkeypatch
):
    app.config["AI_VERIFY_ENABLED"] = False
    oid = _seed_customer_plan_ready_order(app, mobile="")
    monkeypatch.setattr("app.orders.routes._rebuild_garment_quantity_comparison", lambda order, parsed: None)

    monkeypatch.setattr(
        "app.orders.routes.send_customer_plan_webhook",
        lambda **kwargs: {"success": True, "status_code": 200, "message": "ok", "error": None},
    )

    first = auth_client.post(
        f"/orders/{oid}/checklist",
        data=_customer_plan_generate_payload(),
        follow_redirects=False,
    )
    assert first.status_code == 302
    assert "show_mobile_dialog=1" in (first.headers.get("Location", ""))

    second = auth_client.post(
        f"/orders/{oid}/checklist",
        data={
            "current_page": "2",
            "set_customer_mobile_generate_customer_plan": "1",
            "customer_mobile": "+919999888777",
        },
        follow_redirects=True,
    )
    assert second.status_code == 200

    with app.app_context():
        order = Order.query.get(oid)
        assert order.mobile == "+919999888777"
        state = load_checklist_state(order)
        flow = state.get("flow", {}) if isinstance(state, dict) else {}
        assert bool(flow.get("customer_plan_generated", False)) is True


def test_customer_plan_webhook_failure_does_not_block_pdf_generation(app, auth_client, monkeypatch):
    app.config["AI_VERIFY_ENABLED"] = False
    oid = _seed_customer_plan_ready_order(app, mobile="+919876543210")
    monkeypatch.setattr("app.orders.routes._rebuild_garment_quantity_comparison", lambda order, parsed: None)

    monkeypatch.setattr(
        "app.orders.routes.send_customer_plan_webhook",
        lambda **kwargs: {"success": False, "status_code": 500, "message": "n8n down", "error": "http_error"},
    )

    resp = auth_client.post(
        f"/orders/{oid}/checklist",
        data=_customer_plan_generate_payload(),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"webhook failed" in resp.data.lower()

    with app.app_context():
        order = Order.query.get(oid)
        state = load_checklist_state(order)
        flow = state.get("flow", {}) if isinstance(state, dict) else {}
        assert bool(flow.get("customer_plan_generated", False)) is True
        assert str(flow.get("customer_plan_last_send_status", "")) == "failed"
        assert str(flow.get("customer_plan_last_send_error", "")) == "n8n down"
        attachments = Attachment.query.filter_by(order_id=oid).all()
        assert any(str(a.filename).lower().startswith("customer-plan-") for a in attachments)


def test_customer_plan_resend_uses_latest_attachment(app, auth_client, monkeypatch):
    app.config["AI_VERIFY_ENABLED"] = False
    oid = _seed_customer_plan_ready_order(app, mobile="+919876543210")
    monkeypatch.setattr("app.orders.routes._rebuild_garment_quantity_comparison", lambda order, parsed: None)

    monkeypatch.setattr(
        "app.orders.routes.send_customer_plan_webhook",
        lambda **kwargs: {"success": True, "status_code": 200, "message": "ok", "error": None},
    )
    auth_client.post(
        f"/orders/{oid}/checklist",
        data=_customer_plan_generate_payload(),
        follow_redirects=True,
    )

    called = {"count": 0}

    def _resend_webhook(**kwargs):
        called["count"] += 1
        assert str(kwargs["attachment_filename"]).lower().startswith("customer-plan-")
        return {"success": True, "status_code": 200, "message": "ok", "error": None}

    monkeypatch.setattr("app.orders.routes.send_customer_plan_webhook", _resend_webhook)

    resend = auth_client.post(
        f"/orders/{oid}/checklist",
        data={
            "current_page": "2",
            "resend_customer_plan_webhook": "1",
        },
        follow_redirects=True,
    )
    assert resend.status_code == 200
    assert called["count"] == 1
