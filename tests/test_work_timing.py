from datetime import datetime, timedelta
import re

from app.extensions import db
from app.models import Attachment, Order, OrderCheck, WorkTimingAlertEvent, WorkTimingEntry
from app.orders.checklist_cutting import save_checklist_state
from app.orders.services import bootstrap_order_rows
from app.work_timing.services import (
    run_work_timing_overdue_check,
)


def test_update_page_is_public(client):
    resp = client.get("/update")
    assert resp.status_code == 200
    assert b"Time and Action (T&amp;A)" in resp.data
    assert b"Order Status Grid" in resp.data
    assert b"Delivered" in resp.data
    delivered = client.get("/update/delivered")
    assert delivered.status_code == 200
    assert b"Time and Action (T&amp;A)" in delivered.data
    assert b"Order Status Grid" in delivered.data
    assert b"Delivered" in delivered.data


def test_update_status_valid_and_invalid(app, client):
    with app.app_context():
        order = Order(order_id="ORDER-WT-1", customer_name="Work Timing Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        entry = WorkTimingEntry(
            order_id=order.id,
            order_code=order.order_id,
            customer_name=order.customer_name,
            status="PP APPROVED",
        )
        db.session.add(entry)
        db.session.commit()
        entry_id = entry.id

    resp = client.post(
        f"/update/{entry_id}/status",
        data={"status": "READY FOR SHIPPMENT"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        refreshed = WorkTimingEntry.query.get(entry_id)
        # Skip transitions are blocked (PP APPROVED -> READY FOR SHIPPMENT).
        assert refreshed.status == "PP APPROVED"

    allowed_resp = client.post(
        f"/update/{entry_id}/status",
        data={"status": "PRINTING+LASER"},
        follow_redirects=True,
    )
    assert allowed_resp.status_code == 200
    with app.app_context():
        refreshed = WorkTimingEntry.query.get(entry_id)
        assert refreshed.status == "PRINTING+LASER"
        assert refreshed.deadline_at is not None

    resp = client.post(
        f"/update/{entry_id}/status",
        data={"status": "INVALID STATUS"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        refreshed = WorkTimingEntry.query.get(entry_id)
        assert refreshed.status == "PRINTING+LASER"


def test_delivered_orders_hidden_from_update_and_visible_in_delivered_module(app, client):
    with app.app_context():
        active_order = Order(order_id="ORDER-WT-ACTIVE", customer_name="Active Customer")
        delivered_order = Order(order_id="ORDER-WT-DELV", customer_name="Delivered Customer")
        bootstrap_order_rows(active_order)
        bootstrap_order_rows(delivered_order)
        db.session.add(active_order)
        db.session.add(delivered_order)
        db.session.flush()
        db.session.add(
            WorkTimingEntry(
                order_id=active_order.id,
                order_code=active_order.order_id,
                customer_name=active_order.customer_name,
                status="PP APPROVED",
            )
        )
        db.session.add(
            WorkTimingEntry(
                order_id=delivered_order.id,
                order_code=delivered_order.order_id,
                customer_name=delivered_order.customer_name,
                status="DELIVERED",
            )
        )
        db.session.commit()

    update_page = client.get("/update")
    assert update_page.status_code == 200
    assert b"ORDER-WT-ACTIVE" in update_page.data
    assert b"ORDER-WT-DELV" not in update_page.data

    delivered_page = client.get("/update/delivered")
    assert delivered_page.status_code == 200
    assert b"ORDER-WT-DELV" in delivered_page.data
    assert b"ORDER-WT-ACTIVE" not in delivered_page.data


def test_delivered_page_filters_last_week_last_month_and_custom_date(app, client):
    with app.app_context():
        now = datetime.utcnow()
        rows = [
            ("ORDER-WT-D-FLT-1", "Delivered One", now - timedelta(days=2)),
            ("ORDER-WT-D-FLT-2", "Delivered Two", now - timedelta(days=20)),
            ("ORDER-WT-D-FLT-3", "Delivered Three", now - timedelta(days=40)),
        ]
        for order_code, customer_name, delivered_at in rows:
            order = Order(order_id=order_code, customer_name=customer_name)
            bootstrap_order_rows(order)
            db.session.add(order)
            db.session.flush()
            db.session.add(
                WorkTimingEntry(
                    order_id=order.id,
                    order_code=order.order_id,
                    customer_name=order.customer_name,
                    status="DELIVERED",
                    updated_at=delivered_at,
                )
            )
        db.session.commit()

        custom_from = (now - timedelta(days=22)).strftime("%Y-%m-%d")
        custom_to = (now - timedelta(days=18)).strftime("%Y-%m-%d")

    resp_week = client.get("/update/delivered?period=last_week")
    assert resp_week.status_code == 200
    assert b"ORDER-WT-D-FLT-1" in resp_week.data
    assert b"ORDER-WT-D-FLT-2" not in resp_week.data
    assert b"ORDER-WT-D-FLT-3" not in resp_week.data

    resp_month = client.get("/update/delivered?period=last_month")
    assert resp_month.status_code == 200
    assert b"ORDER-WT-D-FLT-1" in resp_month.data
    assert b"ORDER-WT-D-FLT-2" in resp_month.data
    assert b"ORDER-WT-D-FLT-3" not in resp_month.data

    resp_custom = client.get(f"/update/delivered?period=custom&date_from={custom_from}&date_to={custom_to}")
    assert resp_custom.status_code == 200
    assert b"ORDER-WT-D-FLT-1" not in resp_custom.data
    assert b"ORDER-WT-D-FLT-2" in resp_custom.data
    assert b"ORDER-WT-D-FLT-3" not in resp_custom.data


def test_bulk_save_updates_multiple_selected_rows(app, client):
    with app.app_context():
        order_1 = Order(order_id="ORDER-WT-BULK-1", customer_name="Bulk One")
        order_2 = Order(order_id="ORDER-WT-BULK-2", customer_name="Bulk Two")
        bootstrap_order_rows(order_1)
        bootstrap_order_rows(order_2)
        db.session.add(order_1)
        db.session.add(order_2)
        db.session.flush()
        entry_1 = WorkTimingEntry(
            order_id=order_1.id,
            order_code=order_1.order_id,
            customer_name=order_1.customer_name,
            status="PP SAMPLE SENT FOR APPROVAL",
        )
        entry_2 = WorkTimingEntry(
            order_id=order_2.id,
            order_code=order_2.order_id,
            customer_name=order_2.customer_name,
            status="PP SAMPLE SENT FOR APPROVAL",
        )
        db.session.add(entry_1)
        db.session.add(entry_2)
        db.session.commit()

        entry_1_id = entry_1.id
        entry_2_id = entry_2.id

    resp = client.post(
        "/update/bulk-status",
        data={
            "bulk_confirm": "1",
            f"status_{entry_1_id}": "PP APPROVED",
            f"current_status_{entry_1_id}": "PP SAMPLE SENT FOR APPROVAL",
            f"status_{entry_2_id}": "PP APPROVED",
            f"current_status_{entry_2_id}": "PP SAMPLE SENT FOR APPROVAL",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        refreshed_1 = WorkTimingEntry.query.get(entry_1_id)
        refreshed_2 = WorkTimingEntry.query.get(entry_2_id)
        assert refreshed_1.status == "PP APPROVED"
        assert refreshed_2.status == "PP APPROVED"


def test_bulk_save_applies_valid_rows_and_rejects_skip_rows(app, client):
    with app.app_context():
        order_1 = Order(order_id="ORDER-WT-BULK-SAFE-1", customer_name="Bulk Safe One")
        order_2 = Order(order_id="ORDER-WT-BULK-SAFE-2", customer_name="Bulk Safe Two")
        bootstrap_order_rows(order_1)
        bootstrap_order_rows(order_2)
        db.session.add(order_1)
        db.session.add(order_2)
        db.session.flush()
        entry_1 = WorkTimingEntry(
            order_id=order_1.id,
            order_code=order_1.order_id,
            customer_name=order_1.customer_name,
            status="PP APPROVED",
        )
        entry_2 = WorkTimingEntry(
            order_id=order_2.id,
            order_code=order_2.order_id,
            customer_name=order_2.customer_name,
            status="PP APPROVED",
        )
        db.session.add(entry_1)
        db.session.add(entry_2)
        db.session.commit()
        entry_1_id = entry_1.id
        entry_2_id = entry_2.id

    resp = client.post(
        "/update/bulk-status",
        data={
            "bulk_confirm": "1",
            f"status_{entry_1_id}": "PRINTING+LASER",
            f"current_status_{entry_1_id}": "PP APPROVED",
            f"status_{entry_2_id}": "READY FOR SHIPPMENT",
            f"current_status_{entry_2_id}": "PP APPROVED",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Only the immediate next status is allowed." in resp.data

    with app.app_context():
        refreshed_1 = WorkTimingEntry.query.get(entry_1_id)
        refreshed_2 = WorkTimingEntry.query.get(entry_2_id)
        assert refreshed_1.status == "PRINTING+LASER"
        assert refreshed_2.status == "PP APPROVED"


def test_production_plan_generation_creates_work_timing_entry_once(app, auth_client, monkeypatch):
    app.config["INVOICE_RECEIPT_REQUIRED"] = False
    with app.app_context():
        order = Order(order_id="ORDER-WT-TRIGGER", customer_name="Trigger Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        db.session.add(OrderCheck(order_id=order.id, current_page=1))
        db.session.commit()
        order_id = order.id

        save_checklist_state(
            order,
            {
                "flow": {
                    "customer_plan_generated": True,
                    "customer_plan_attachment_id": None,
                    "customer_approved": True,
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
        db.session.commit()

    def fake_render_and_store_plan_pdf(order, plan_slug, display_order_id=None):
        return (
            b"%PDF-1.4\n%%EOF",
            Attachment(
                order_id=order.id,
                filename=f"{plan_slug}-{order.order_id}-V1.pdf",
                mime_type="application/pdf",
                storage_path=f"order://{order.id}/generated_order_sheets/{plan_slug}.pdf",
            ),
        )

    monkeypatch.setattr("app.orders.routes._render_and_store_plan_pdf", fake_render_and_store_plan_pdf)
    monkeypatch.setattr("app.orders.routes.ingest_production_plan_for_order", lambda **kwargs: 1)

    for _ in range(2):
        resp = auth_client.post(
            f"/orders/{order_id}/checklist",
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
        rows = WorkTimingEntry.query.filter_by(order_id=order_id).all()
        assert len(rows) == 1
        assert rows[0].order_code
        assert rows[0].customer_name == "Trigger Customer"
        assert rows[0].status == "PP SAMPLE SENT FOR APPROVAL"
        assert rows[0].deadline_at is not None
        delta = rows[0].deadline_at - rows[0].status_updated_at
        assert timedelta(hours=23, minutes=59) <= delta <= timedelta(hours=24, minutes=1)

    resp = auth_client.get("/update")
    assert resp.status_code == 200
    # Per-row dropdown is restricted to current + next status.
    assert b"PP SAMPLE SENT FOR APPROVAL" in resp.data
    assert b"PP APPROVED" in resp.data
    assert b"PRINTING+LASER" not in resp.data


def test_update_status_rejects_backward_transition(app, client):
    with app.app_context():
        order = Order(order_id="ORDER-WT-BACK", customer_name="Back Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        entry = WorkTimingEntry(
            order_id=order.id,
            order_code=order.order_id,
            customer_name=order.customer_name,
            status="PRINTING+LASER",
            status_updated_at=datetime.utcnow(),
            deadline_at=datetime.utcnow() + timedelta(hours=96),
            escalation_state="NONE",
        )
        db.session.add(entry)
        db.session.commit()
        entry_id = entry.id

    resp = client.post(
        f"/update/{entry_id}/status",
        data={"status": "PP APPROVED"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        refreshed = WorkTimingEntry.query.get(entry_id)
        assert refreshed.status == "PRINTING+LASER"


def test_update_status_same_value_is_noop(app, client):
    with app.app_context():
        order = Order(order_id="ORDER-WT-NOOP", customer_name="Noop Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        before = datetime.utcnow()
        entry = WorkTimingEntry(
            order_id=order.id,
            order_code=order.order_id,
            customer_name=order.customer_name,
            status="PP APPROVED",
            status_updated_at=before,
            deadline_at=before + timedelta(hours=72),
            escalation_state="NONE",
        )
        db.session.add(entry)
        db.session.commit()
        entry_id = entry.id

    resp = client.post(
        f"/update/{entry_id}/status",
        data={"status": "PP APPROVED"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Status is already set to that value." in resp.data

    with app.app_context():
        refreshed = WorkTimingEntry.query.get(entry_id)
        assert refreshed.status == "PP APPROVED"
        assert refreshed.status_updated_at == before


def test_update_dropdown_shows_only_current_and_next(app, client):
    with app.app_context():
        order = Order(order_id="ORDER-WT-DD-1", customer_name="Dropdown Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        entry = WorkTimingEntry(
            order_id=order.id,
            order_code=order.order_id,
            customer_name=order.customer_name,
            status="PP APPROVED",
        )
        db.session.add(entry)
        db.session.commit()
        entry_id = entry.id

    resp = client.get("/update")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    select_match = re.search(
        rf'<select name="status_{entry_id}"[^>]*>(.*?)</select>',
        html,
        flags=re.DOTALL,
    )
    assert select_match is not None
    select_html = select_match.group(1)
    assert "PP APPROVED" in select_html
    assert "PRINTING+LASER" in select_html
    assert "PP SAMPLE SENT FOR APPROVAL" not in select_html
    assert "READY FOR SHIPPMENT" not in select_html
    assert "DELIVERED" not in select_html


def test_update_page_renders_timer_data_attributes_for_timed_and_untimed_rows(app, client):
    with app.app_context():
        now = datetime.utcnow()

        timed_order = Order(order_id="ORDER-WT-TIMER-TIMED", customer_name="Timed Customer")
        untimed_order = Order(order_id="ORDER-WT-TIMER-UNTIMED", customer_name="Untimed Customer")
        bootstrap_order_rows(timed_order)
        bootstrap_order_rows(untimed_order)
        db.session.add(timed_order)
        db.session.add(untimed_order)
        db.session.flush()

        timed_entry = WorkTimingEntry(
            order_id=timed_order.id,
            order_code=timed_order.order_id,
            customer_name=timed_order.customer_name,
            status="PP APPROVED",
            status_updated_at=now,
            deadline_at=now + timedelta(hours=72),
        )
        untimed_entry = WorkTimingEntry(
            order_id=untimed_order.id,
            order_code=untimed_order.order_id,
            customer_name=untimed_order.customer_name,
            status="READY FOR SHIPPMENT",
            status_updated_at=now,
            deadline_at=None,
        )
        db.session.add(timed_entry)
        db.session.add(untimed_entry)
        db.session.commit()

    resp = client.get("/update")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert re.search(r'data-total-seconds="\d+"', html) is not None
    assert 'data-total-seconds=""' in html
    assert 'data-deadline=""' in html


class _FakeNotifier:
    def __init__(self):
        self.calls = []

    def send_overdue_alert(self, target, payload):
        self.calls.append((target, payload))
        return {"delivery_mode": "log", "delivery_result": "logged"}


def test_overdue_checker_escalates_giri_then_md_once(app):
    with app.app_context():
        order = Order(order_id="ORDER-WT-ALERT", customer_name="Alert Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        base = datetime.utcnow() - timedelta(hours=1)
        entry = WorkTimingEntry(
            order_id=order.id,
            order_code=order.order_id,
            customer_name=order.customer_name,
            status="PP SAMPLE SENT FOR APPROVAL",
            status_updated_at=base,
            deadline_at=base,
            escalation_state="NONE",
        )
        db.session.add(entry)
        db.session.commit()
        entry_id = entry.id

        notifier = _FakeNotifier()
        first = run_work_timing_overdue_check(now=base + timedelta(minutes=2), notifier=notifier)
        assert first["giri_sent"] == 1
        assert first["md_sent"] == 0

        second = run_work_timing_overdue_check(
            now=base + timedelta(hours=12, minutes=3),
            notifier=notifier,
        )
        assert second["giri_sent"] == 0
        assert second["md_sent"] == 1

        third = run_work_timing_overdue_check(
            now=base + timedelta(hours=24),
            notifier=notifier,
        )
        assert third["giri_sent"] == 0
        assert third["md_sent"] == 0

        refreshed = WorkTimingEntry.query.get(entry_id)
        assert refreshed.escalation_state == "MD_SENT"
        events = WorkTimingAlertEvent.query.filter_by(entry_id=entry_id).order_by(WorkTimingAlertEvent.id.asc()).all()
        assert len(events) == 2
        assert events[0].target == "GIRI"
        assert events[1].target == "MD"


def test_overdue_checker_webhook_ack_persists_delivery_metadata(app, monkeypatch):
    with app.app_context():
        app.config["WORK_TIMING_ALERT_MODE"] = "webhook"
        app.config["WORK_TIMING_WEBHOOK_URL"] = "https://automation.example/webhook/work-timing-alert"
        app.config["WORK_TIMING_WEBHOOK_TOKEN"] = "token-123"
        app.config["WORK_TIMING_WEBHOOK_TIMEOUT_SECONDS"] = 9
        app.config["WORK_TIMING_GIRI_CONTACT"] = "+919999000001"

        order = Order(order_id="ORDER-WT-WEBHOOK-ACK", customer_name="Ack Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        base = datetime.utcnow() - timedelta(hours=1)
        entry = WorkTimingEntry(
            order_id=order.id,
            order_code=order.order_id,
            customer_name=order.customer_name,
            status="PP SAMPLE SENT FOR APPROVAL",
            status_updated_at=base,
            deadline_at=base,
            escalation_state="NONE",
        )
        db.session.add(entry)
        db.session.commit()
        entry_id = int(entry.id)

        captured = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "ok": True,
                    "delivery_mode": "whatsapp",
                    "delivery_result": "queued",
                    "provider_message_id": "wamid.HBgMXYZ",
                }

        def _fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = dict(json or {})
            captured["headers"] = dict(headers or {})
            captured["timeout"] = timeout
            return _Resp()

        monkeypatch.setattr("app.work_timing.services.requests.post", _fake_post)

        result = run_work_timing_overdue_check(now=base + timedelta(minutes=2))
        assert result["giri_sent"] == 1
        assert captured["url"] == "https://automation.example/webhook/work-timing-alert"
        assert captured["headers"]["X-Internal-Token"] == "token-123"
        assert captured["timeout"] == 9
        assert captured["json"]["target"] == "GIRI"
        assert captured["json"]["contact"] == "+919999000001"
        assert "alert_id" in captured["json"]

        event = WorkTimingAlertEvent.query.filter_by(entry_id=entry_id).order_by(WorkTimingAlertEvent.id.desc()).first()
        assert event is not None
        assert event.delivery_mode == "whatsapp"
        assert event.delivery_result == "queued"
        assert event.provider_message_id == "wamid.HBgMXYZ"


def test_overdue_checker_webhook_missing_token_records_failure(app):
    with app.app_context():
        app.config["WORK_TIMING_ALERT_MODE"] = "webhook"
        app.config["WORK_TIMING_WEBHOOK_URL"] = "https://automation.example/webhook/work-timing-alert"
        app.config["WORK_TIMING_WEBHOOK_TOKEN"] = ""
        app.config["WORK_TIMING_GIRI_CONTACT"] = "+919999000001"

        order = Order(order_id="ORDER-WT-WEBHOOK-NOTOKEN", customer_name="No Token Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()
        base = datetime.utcnow() - timedelta(hours=1)
        entry = WorkTimingEntry(
            order_id=order.id,
            order_code=order.order_id,
            customer_name=order.customer_name,
            status="PP SAMPLE SENT FOR APPROVAL",
            status_updated_at=base,
            deadline_at=base,
            escalation_state="NONE",
        )
        db.session.add(entry)
        db.session.commit()
        entry_id = int(entry.id)

        result = run_work_timing_overdue_check(now=base + timedelta(minutes=2))
        assert result["giri_sent"] == 1

        event = WorkTimingAlertEvent.query.filter_by(entry_id=entry_id).order_by(WorkTimingAlertEvent.id.desc()).first()
        assert event is not None
        assert event.delivery_mode == "webhook"
        assert event.delivery_result == "webhook_token_missing"
