def test_invoice_entry_renders_login_for_unauthenticated(client):
    resp = client.get("/invoice")
    assert resp.status_code == 200
    assert b"Invoice" in resp.data


def test_invoice_login_admin_redirects_to_preview(client):
    resp = client.post(
        "/invoice",
        data={"username": "admin", "password": "Password123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invoice PDF layout is being updated" in resp.data


def test_invoice_login_non_admin_is_rejected(client):
    resp = client.post(
        "/invoice",
        data={"username": "giri", "password": "Password123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Only admin can access invoice module." in resp.data


def test_invoice_preview_redirects_to_invoice_login_when_unauthenticated(client):
    resp = client.get("/invoice/preview")
    assert resp.status_code == 302
    assert "/invoice?next=/invoice/preview" in resp.headers.get("Location", "")


def test_invoice_preview_blocks_non_admin(operator_client):
    resp = operator_client.get("/invoice/preview")
    assert resp.status_code == 403


def test_invoice_entry_redirects_authenticated_admin_to_preview(auth_client):
    resp = auth_client.get("/invoice")
    assert resp.status_code == 302
    assert "/invoice/preview" in resp.headers.get("Location", "")
