from app.integrations.customer_plan_webhook import send_customer_plan_webhook


def test_customer_plan_webhook_sends_multipart_with_token(app, monkeypatch):
    app.config["CUSTOMER_PLAN_WEBHOOK_ENABLED"] = True
    app.config["CUSTOMER_PLAN_WEBHOOK_URL"] = "https://n8n.example/webhook/customer-plan"
    app.config["CUSTOMER_PLAN_WEBHOOK_TOKEN"] = "secret-token"
    app.config["CUSTOMER_PLAN_WEBHOOK_TIMEOUT_SECONDS"] = 12

    captured = {}

    class _Resp:
        status_code = 200

    def _fake_post(url, data=None, files=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = dict(data or {})
        captured["files"] = files
        captured["headers"] = dict(headers or {})
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr("app.integrations.customer_plan_webhook.requests.post", _fake_post)

    with app.app_context():
        result = send_customer_plan_webhook(
            order_id=12,
            enquiry_id="E2E-001",
            customer_name="Demo Customer",
            customer_mobile="+919876543210",
            attachment_filename="customer-plan-demo.pdf",
            pdf_bytes=b"%PDF-1.4\n%%EOF",
        )

    assert result["success"] is True
    assert captured["url"] == "https://n8n.example/webhook/customer-plan"
    assert captured["headers"]["X-Webhook-Token"] == "secret-token"
    assert captured["timeout"] == 12
    assert captured["data"]["order_id"] == "12"
    assert captured["data"]["enquiry_id"] == "E2E-001"
    assert captured["data"]["customer_mobile"] == "+919876543210"
    assert "file" in captured["files"]


def test_customer_plan_webhook_http_error_returns_failure(app, monkeypatch):
    app.config["CUSTOMER_PLAN_WEBHOOK_ENABLED"] = True
    app.config["CUSTOMER_PLAN_WEBHOOK_URL"] = "https://n8n.example/webhook/customer-plan"
    app.config["CUSTOMER_PLAN_WEBHOOK_TOKEN"] = "secret-token"

    class _Resp:
        status_code = 500

    monkeypatch.setattr(
        "app.integrations.customer_plan_webhook.requests.post",
        lambda *args, **kwargs: _Resp(),
    )

    with app.app_context():
        result = send_customer_plan_webhook(
            order_id=12,
            enquiry_id="E2E-001",
            customer_name="Demo Customer",
            customer_mobile="+919876543210",
            attachment_filename="customer-plan-demo.pdf",
            pdf_bytes=b"%PDF-1.4\n%%EOF",
        )

    assert result["success"] is False
    assert result["status_code"] == 500

