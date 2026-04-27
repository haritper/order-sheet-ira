from types import SimpleNamespace

from app.exports import services as export_services
from app.invoice import services as invoice_services


def test_extract_payment_details_from_text():
    payload = """
    PAYMENT RECEIVED ($) $958.00
    PAYMENT MODE ZELLE
    PAID ON 2ND APRIL 2026
    TRANSACTION ID 28677308895
    """
    extracted = invoice_services.extract_payment_details_from_text(payload)
    assert extracted["transaction_id"] == "28677308895"
    assert extracted["amount_paid"] == "958.00"
    assert extracted["payment_mode"] == "ZELLE"
    assert extracted["paid_on"] == "2ND APRIL 2026"


def test_merge_payment_fields_manual_override():
    extracted = {
        "transaction_id": "AUTO-111",
        "amount_paid": "100.00",
        "payment_mode": "BANK",
        "paid_on": "1ST JAN 2026",
    }
    saved = {"transaction_id": "SAVED-222", "payment_mode": "ZELLE"}
    manual = {"transaction_id": "MANUAL-333", "amount_paid": "250.50"}
    merged = invoice_services.merge_payment_fields(extracted, saved, manual)
    assert merged["transaction_id"] == "MANUAL-333"
    assert merged["amount_paid"] == "250.50"
    assert merged["payment_mode"] == "ZELLE"
    assert merged["paid_on"] == "1ST JAN 2026"


def test_extract_receipt_payment_details_uses_vision_fallback(monkeypatch):
    monkeypatch.setattr(invoice_services, "read_bytes", lambda _path: b"fake-image-bytes")
    monkeypatch.setattr(
        invoice_services,
        "_extract_with_openai_vision",
        lambda _attachment, _payload: {
            "transaction_id": "VISION-TXN-987",
            "amount_paid": "500.00",
            "payment_mode": "UPI",
            "paid_on": "3RD APRIL 2026",
        },
    )

    attachment = SimpleNamespace(
        mime_type="image/png",
        filename="receipt.png",
        storage_path="dummy://receipt.png",
    )
    extracted = invoice_services.extract_receipt_payment_details(attachment)
    assert extracted["transaction_id"] == "VISION-TXN-987"
    assert extracted["amount_paid"] == "500.00"
    assert extracted["payment_mode"] == "UPI"
    assert extracted["paid_on"] == "3RD APRIL 2026"


def test_format_invoice_paid_on_removes_time_and_formats_ordinal():
    formatted = export_services._format_invoice_paid_on("18 Apr 2026, 8:56 am")
    assert formatted == "18TH APRIL 2026"
