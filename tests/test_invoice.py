from datetime import date
from io import BytesIO
import re

from fpdf import FPDF
import pytest
from pypdf import PdfReader

from app.extensions import db
from app.invoice.services import save_invoice_state
from app.models import Attachment, Order, OrderNumberCounter
from app.orders.checklist_cutting import save_checklist_state
from app.orders.check_state import get_or_create_order_check
from app.orders.services import bootstrap_order_rows
from app.pricing_integration import db as pricing_db
from app.storage import ORDER_DOCUMENT_SECTION, save_order_file


@pytest.fixture(autouse=True)
def _isolate_pricing_database(app, tmp_path):
    app.config["PRICING_DATABASE"] = str((tmp_path / "pricing_invoice_tests.db").resolve())
    with app.app_context():
        pricing_db.init_db()
    yield


def _build_receipt_pdf_bytes() -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    lines = [
        "PAYMENT RECEIVED ($) $958.00",
        "PAYMENT MODE ZELLE",
        "PAID ON 2ND APRIL 2026",
        "TRANSACTION ID 28677308895",
    ]
    for line in lines:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _create_invoice_order(app, *, order_code: str, ira_order_code: str | None = None, with_receipt: bool = False):
    with app.app_context():
        order = Order(
            order_id=order_code,
            production_order_id=ira_order_code,
            enquiry_date=date(2026, 3, 30),
            customer_name="RAVI T",
            mobile="5103617863",
            shipping_address="4208 WOOD FERN LN",
            city="BUFORD",
            state="GA",
            zip_code="30519",
            country="USA",
        )
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.flush()

        receipt_attachment_id = None
        if with_receipt:
            content = _build_receipt_pdf_bytes()
            filename = "invoice-receipt-test.pdf"
            storage_path = save_order_file(
                order.id,
                ORDER_DOCUMENT_SECTION,
                filename,
                content,
                content_type="application/pdf",
            )
            receipt = Attachment(
                order_id=order.id,
                filename=filename,
                mime_type="application/pdf",
                storage_path=storage_path,
            )
            db.session.add(receipt)
            db.session.flush()
            receipt_attachment_id = int(receipt.id)

        order.order_check = get_or_create_order_check(order)
        save_checklist_state(
            order,
            {
                "flow": {
                    "customer_plan_generated": True,
                    "customer_plan_attachment_id": None,
                    "customer_approved": True,
                    "invoice_receipt_uploaded": bool(receipt_attachment_id),
                    "invoice_receipt_attachment_id": receipt_attachment_id,
                    "invoice_receipt_filename": "invoice-receipt-test.pdf" if receipt_attachment_id else "",
                    "shipping_address": "4208 WOOD FERN LN",
                    "city": "BUFORD",
                    "state": "GA",
                    "zip_code": "30519",
                    "country": "USA",
                    "production_plan_generated": False,
                    "production_plan_attachment_id": None,
                }
            },
        )
        db.session.commit()
        return int(order.id)


def _seed_pricing_total(app, order_id: int, quoted_subtotal: float):
    with app.app_context():
        pricing_db.init_db()
        manager = pricing_db.execute(
            "SELECT id FROM users WHERE username = 'manager' LIMIT 1"
        ).fetchone()
        manager_id = int(manager["id"]) if manager is not None else 1
        pricing_db.execute(
            """
            INSERT OR REPLACE INTO orders (
                id,
                order_number,
                external_order_id,
                customer_name,
                enquiry_date,
                mobile,
                shipping_address,
                destination_city,
                destination_state,
                destination_country,
                status,
                uploaded_filename,
                extracted_payload_json,
                suggested_subtotal,
                quoted_subtotal,
                shipping_fee,
                duty_fee,
                final_landed_cost,
                final_margin,
                created_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(order_id),
                f"PRICING-{order_id}",
                f"order-sheet:{order_id}",
                "Invoice Test",
                "2026-04-01",
                "",
                "",
                "",
                "",
                "USA",
                "quoted",
                f"pricing-order-{order_id}.pdf",
                "{}",
                float(quoted_subtotal),
                float(quoted_subtotal),
                0.0,
                0.0,
                float(quoted_subtotal),
                0.0,
                manager_id,
            ),
        )


def test_invoice_entry_renders_login_for_unauthenticated(client):
    resp = client.get("/invoice")
    assert resp.status_code == 200
    assert b"Invoice" in resp.data


def test_invoice_login_admin_redirects_to_dashboard(client):
    resp = client.post(
        "/invoice",
        data={"username": "admin", "password": "Password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/invoice/dashboard" in resp.headers.get("Location", "")


def test_invoice_login_non_admin_is_rejected(client):
    resp = client.post(
        "/invoice",
        data={"username": "giri", "password": "Password123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Only admin can access invoice module." in resp.data


def test_invoice_preview_route_removed(client):
    resp = client.get("/invoice/preview")
    assert resp.status_code == 404


def test_invoice_entry_redirects_authenticated_admin_to_dashboard(auth_client):
    resp = auth_client.get("/invoice")
    assert resp.status_code == 302
    assert "/invoice/dashboard" in resp.headers.get("Location", "")


def test_invoice_dashboard_blocks_non_admin(operator_client):
    resp = operator_client.get("/invoice/dashboard")
    assert resp.status_code == 403


def test_invoice_dashboard_lists_orders_without_search(app, auth_client):
    _create_invoice_order(
        app,
        order_code="POD-26-900-LIST-ONLY",
        ira_order_code="202604-900-LIST-ONLY",
        with_receipt=True,
    )
    resp = auth_client.get("/invoice/dashboard")
    assert resp.status_code == 200
    assert b"Only orders with uploaded invoice receipt are shown here." in resp.data
    assert b"POD-26-900-LIST-ONLY" in resp.data
    assert b"202604-900-LIST-ONLY" in resp.data
    assert b"Generate &amp; Download" in resp.data


def test_invoice_dashboard_shows_enquiry_id_in_order_list(app, auth_client):
    order_id = _create_invoice_order(app, order_code="POD-26-413-MOLDTEK-RANGERS", with_receipt=True)
    resp = auth_client.get("/invoice/dashboard")
    assert resp.status_code == 200
    assert b"POD-26-413-MOLDTEK-RANGERS" in resp.data
    assert order_id > 0


def test_invoice_dashboard_shows_ira_order_id_in_order_list(app, auth_client):
    _create_invoice_order(
        app,
        order_code="POD-26-413-MOLDTEK-RANGERS",
        ira_order_code="202604-380-MOLDTEK-RANGERS",
        with_receipt=True,
    )
    resp = auth_client.get("/invoice/dashboard")
    assert resp.status_code == 200
    assert b"202604-380-MOLDTEK-RANGERS" in resp.data


def test_invoice_dashboard_hides_orders_without_receipt(app, auth_client):
    _create_invoice_order(app, order_code="POD-26-500-WITHOUT-RECEIPT", with_receipt=False)
    _create_invoice_order(app, order_code="POD-26-501-WITH-RECEIPT", with_receipt=True)
    resp = auth_client.get("/invoice/dashboard")
    assert resp.status_code == 200
    assert b"POD-26-501-WITH-RECEIPT" in resp.data
    assert b"POD-26-500-WITHOUT-RECEIPT" not in resp.data


def test_invoice_download_blocks_when_receipt_missing(app, auth_client):
    oid = _create_invoice_order(app, order_code="POD-26-500-MISSING-RECEIPT", with_receipt=False)
    resp = auth_client.post(
        f"/invoice/{oid}/download",
        data={
            "transaction_id": "12345",
            "amount_paid": "500.00",
            "payment_mode": "ZELLE",
            "paid_on": "2ND APRIL 2026",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Upload invoice receipt in checklist before generating invoice." in resp.data


def test_invoice_pdf_download_persists_versioned_exports(app, auth_client):
    oid = _create_invoice_order(
        app,
        order_code="POD-26-413-MOLDTEK-RANGERS",
        ira_order_code="202604-380-MOLDTEK-RANGERS",
        with_receipt=True,
    )
    _seed_pricing_total(app, oid, 1000.00)

    first = auth_client.post(
        f"/invoice/{oid}/download",
        data={
            "transaction_id": "28677308895",
            "amount_paid": "958.00",
            "payment_mode": "ZELLE",
            "paid_on": "2ND APRIL 2026",
        },
        follow_redirects=False,
    )
    assert first.status_code == 200
    assert first.headers.get("Content-Type", "").startswith("application/pdf")
    parsed = PdfReader(BytesIO(first.data))
    page_text = parsed.pages[0].extract_text() or ""
    assert "ORDER CONFIRMATION" in page_text
    assert "TRANSACTION ID" in page_text
    assert "28677308895" in page_text

    second = auth_client.post(
        f"/invoice/{oid}/download",
        data={
            "transaction_id": "28677308895",
            "amount_paid": "958.00",
            "payment_mode": "ZELLE",
            "paid_on": "2ND APRIL 2026",
        },
        follow_redirects=False,
    )
    assert second.status_code == 200

    with app.app_context():
        exports = [
            row.filename
            for row in Attachment.query.filter_by(order_id=oid, mime_type="application/pdf").all()
            if re.match(r"^invoice-.*-V\d+\.pdf$", str(row.filename or ""), flags=re.IGNORECASE)
        ]
        assert len(exports) == 2
        assert any(name.endswith("-V1.pdf") for name in exports)
        assert any(name.endswith("-V2.pdf") for name in exports)


def test_invoice_download_ignores_stale_saved_state_when_receipt_changes(app, auth_client, monkeypatch):
    oid = _create_invoice_order(
        app,
        order_code="POD-26-901-STALE-STATE",
        ira_order_code="202604-901-STALE-STATE",
        with_receipt=True,
    )
    _seed_pricing_total(app, oid, 500.00)

    with app.app_context():
        order = Order.query.get(oid)
        assert order is not None
        save_invoice_state(
            order,
            {
                "transaction_id": "OLD-TXN-111",
                "amount_paid": "999.00",
                "payment_mode": "BANK",
                "paid_on": "1ST JAN 2026",
            },
            receipt_attachment_id=999999,
        )

    monkeypatch.setattr(
        "app.invoice.routes.extract_receipt_payment_details",
        lambda _attachment: {
            "transaction_id": "NEW-TXN-222",
            "amount_paid": "200.00",
            "payment_mode": "UPI",
            "paid_on": "18 APR 2026",
        },
    )

    resp = auth_client.post(f"/invoice/{oid}/download", data={}, follow_redirects=False)
    assert resp.status_code == 200
    parsed = PdfReader(BytesIO(resp.data))
    page_text = (parsed.pages[0].extract_text() or "").upper()
    assert "NEW-TXN-222" in page_text
    assert "OLD-TXN-111" not in page_text
    assert "200.00" in page_text


def test_invoice_pdf_uses_checklist_shipping_fallback_when_order_shipping_missing(app, auth_client):
    oid = _create_invoice_order(
        app,
        order_code="POD-26-902-SHIPPING-FALLBACK",
        ira_order_code="202604-902-SHIPPING-FALLBACK",
        with_receipt=True,
    )
    _seed_pricing_total(app, oid, 958.00)
    with app.app_context():
        order = Order.query.get(oid)
        assert order is not None
        order.shipping_address = None
        order.city = None
        order.state = None
        order.zip_code = None
        db.session.commit()

    resp = auth_client.post(
        f"/invoice/{oid}/download",
        data={
            "transaction_id": "28677308895",
            "amount_paid": "958.00",
            "payment_mode": "ZELLE",
            "paid_on": "2ND APRIL 2026",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    parsed = PdfReader(BytesIO(resp.data))
    page_text = (parsed.pages[0].extract_text() or "").upper()
    assert "4208 WOOD FERN LN" in page_text
    assert "BUFORD" in page_text
    assert "30519" in page_text


def test_invoice_download_consumes_invoice_counter_and_embeds_generated_invoice_number(app, auth_client):
    oid = _create_invoice_order(
        app,
        order_code="POD-26-903-INVOICE-COUNTER",
        ira_order_code="202604-903-INVOICE-COUNTER",
        with_receipt=True,
    )
    _seed_pricing_total(app, oid, 958.00)
    with app.app_context():
        settings = OrderNumberCounter.query.first()
        if settings is None:
            settings = OrderNumberCounter(
                pod_next_number=1,
                ira_next_number=1,
                invoice_next_number=77,
                sequence_width=3,
            )
            db.session.add(settings)
        else:
            settings.invoice_next_number = 77
        db.session.commit()

    resp = auth_client.post(
        f"/invoice/{oid}/download",
        data={
            "transaction_id": "TXN-COUNTER-1",
            "amount_paid": "958.00",
            "payment_mode": "ZELLE",
            "paid_on": "2ND APRIL 2026",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    parsed = PdfReader(BytesIO(resp.data))
    page_text = parsed.pages[0].extract_text() or ""
    assert "INV-2026-0077" in page_text

    with app.app_context():
        settings = OrderNumberCounter.query.first()
        assert settings is not None
        assert int(settings.invoice_next_number) == 78


def test_invoice_download_blocks_when_pricing_total_missing(app, auth_client):
    oid = _create_invoice_order(
        app,
        order_code="POD-26-904-MISSING-PRICING",
        ira_order_code="202604-904-MISSING-PRICING",
        with_receipt=True,
    )
    resp = auth_client.post(
        f"/invoice/{oid}/download",
        data={
            "transaction_id": "TXN-MISSING-PRICING",
            "amount_paid": "100.00",
            "payment_mode": "UPI",
            "paid_on": "18 APR 2026",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Pricing total is missing for this order." in resp.data


def test_invoice_pdf_reconciliation_uses_pricing_total_for_partial_and_full(app, auth_client):
    oid = _create_invoice_order(
        app,
        order_code="POD-26-905-RECONCILE",
        ira_order_code="202604-905-RECONCILE",
        with_receipt=True,
    )
    _seed_pricing_total(app, oid, 1000.00)

    partial = auth_client.post(
        f"/invoice/{oid}/download",
        data={
            "transaction_id": "TXN-PARTIAL-1",
            "amount_paid": "200.00",
            "payment_mode": "UPI",
            "paid_on": "18 APR 2026",
        },
        follow_redirects=False,
    )
    assert partial.status_code == 200
    partial_text = (PdfReader(BytesIO(partial.data)).pages[0].extract_text() or "").upper()
    assert "AMOUNT ($)" in partial_text
    assert "1000.00" in partial_text
    assert "PAYMENT RECEIVED ($)" in partial_text
    assert "200.00" in partial_text
    assert "BALANCE ($)" in partial_text
    assert "800.00" in partial_text
    assert "PARTIALLY PAID" in partial_text

    full = auth_client.post(
        f"/invoice/{oid}/download",
        data={
            "transaction_id": "TXN-FULL-1",
            "amount_paid": "1200.00",
            "payment_mode": "UPI",
            "paid_on": "18 APR 2026",
        },
        follow_redirects=False,
    )
    assert full.status_code == 200
    full_text = (PdfReader(BytesIO(full.data)).pages[0].extract_text() or "").upper()
    assert "1000.00" in full_text
    assert "1200.00" in full_text
    assert "BALANCE ($)" in full_text
    assert "0.00" in full_text
    assert "FULLY PAID" in full_text
