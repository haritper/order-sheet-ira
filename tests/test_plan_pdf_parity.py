from pathlib import Path

from app.extensions import db
from app.exports.services import collect_plan_render_stats
from app.models import Attachment, BrandingSpec, Order
from app.orders import routes as order_routes
from app.orders.services import bootstrap_order_rows


def _seed_order_with_design_and_images(tmp_path: Path) -> Order:
    order = Order(order_id="ORDER-PLAN-PARITY", customer_name="Parity Customer")
    bootstrap_order_rows(order)
    db.session.add(order)
    db.session.flush()

    # Files do not need real image bytes for stats collection; only presence as image attachments.
    front_path = tmp_path / "front.png"
    trouser_path = tmp_path / "trouser-right.png"
    cap_path = tmp_path / "cap-front.png"
    for p in (front_path, trouser_path, cap_path):
        p.write_bytes(b"fake-image")
        db.session.add(
            Attachment(
                order_id=order.id,
                filename=p.name,
                mime_type="image/png",
                storage_path=str(p),
            )
        )

    db.session.add(
        BrandingSpec(
            order_id=order.id,
            garment_type="Playing Jersey",
            gender="MENS",
            sleeve_type="HALF",
            front_image_path=str(front_path),
            fabric="INTERLOCK 160 GSM",
        )
    )
    db.session.add(
        BrandingSpec(
            order_id=order.id,
            garment_type="Travel Trouser",
            gender="MENS",
            sleeve_type="",
            right_image_path=str(trouser_path),
            fabric="CORSA 220 GSM",
        )
    )
    db.session.add(
        BrandingSpec(
            order_id=order.id,
            garment_type="Cap",
            gender="MENS",
            sleeve_type="",
            front_image_path=str(cap_path),
            fabric="CORD SANWICH",
        )
    )
    db.session.commit()
    return order


def test_collect_plan_render_stats_nonzero_for_design_sections(app, tmp_path):
    with app.app_context():
        order = _seed_order_with_design_and_images(tmp_path)
        stats = collect_plan_render_stats(order)
        assert stats["tshirt_count"] > 0
        assert stats["trouser_count"] > 0
        assert stats["accessory_count"] > 0
        assert stats["missing_image_paths"] == 0


def test_render_and_store_plan_pdf_uses_same_renderer_for_customer_and_production(app, monkeypatch, caplog):
    with app.app_context():
        order = Order(order_id="ORDER-PLAN-HELPER", customer_name="Helper Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.commit()

        render_calls = []

        variants = []

        def _fake_render(target_order, pdf_variant="order_sheet", display_order_id=None):
            render_calls.append(target_order.id)
            variants.append(str(pdf_variant))
            return b"%PDF-1.4\n%%EOF"

        def _fake_save_plan_pdf(target_order, pdf_bytes, plan_slug, display_order_id=None):
            assert pdf_bytes.startswith(b"%PDF")
            label = str(display_order_id or target_order.order_id)
            return Attachment(
                order_id=target_order.id,
                filename=f"{plan_slug}-{label}-fake.pdf",
                mime_type="application/pdf",
                storage_path=f"/tmp/{plan_slug}-{label}.pdf",
            )

        monkeypatch.setattr(order_routes, "render_order_pdf", _fake_render)
        monkeypatch.setattr(order_routes, "save_plan_pdf", _fake_save_plan_pdf)
        monkeypatch.setattr(
            order_routes,
            "collect_plan_render_stats",
            lambda _order: {
                "tshirt_count": 2,
                "trouser_count": 1,
                "accessory_count": 1,
                "missing_image_paths": 0,
            },
        )

        caplog.set_level("INFO")
        customer_pdf, customer_attachment = order_routes._render_and_store_plan_pdf(order, "customer-plan")
        production_pdf, production_attachment = order_routes._render_and_store_plan_pdf(order, "production-plan")

        assert customer_pdf.startswith(b"%PDF")
        assert production_pdf.startswith(b"%PDF")
        assert customer_attachment.filename.startswith("customer-plan-")
        assert production_attachment.filename.startswith("production-plan-")
        assert render_calls == [order.id, order.id]
        assert variants == ["customer-plan", "production-plan"]
        assert "plan_slug=customer-plan" in caplog.text
        assert "plan_slug=production-plan" in caplog.text
        assert "missing_image_paths=0" in caplog.text


def test_file_uri_resolves_relative_paths_against_project_root(app, tmp_path):
    from app.exports import services

    with app.app_context():
        project_root = Path(app.root_path).parent
        rel_file = Path("data/uploads/test-relative-image.png")
        abs_file = project_root / rel_file
        abs_file.parent.mkdir(parents=True, exist_ok=True)
        abs_file.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        uri = services._file_uri(str(rel_file))
        assert uri is not None
        assert uri.startswith("data:image/png;base64,")

        missing_uri = services._file_uri("data/uploads/does-not-exist.png")
        assert missing_uri is None


def test_render_order_pdf_hides_shipping_for_customer_plan(app, monkeypatch):
    from app.exports import services

    with app.app_context():
        order = Order(order_id="ORDER-CUSTOMER-HIDE-SHIPPING", customer_name="Hide Shipping")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.commit()

        captured = {}

        def _fake_render_template(_tpl, **ctx):
            captured.update(ctx)
            return "<html></html>"

        monkeypatch.setattr(services, "render_template", _fake_render_template)
        monkeypatch.setattr(services, "HTML", None)
        monkeypatch.setattr(services, "sync_playwright", None)

        services.render_order_pdf(order, pdf_variant="customer-plan")
        assert captured.get("show_shipping_details") is False

        captured.clear()
        services.render_order_pdf(order, pdf_variant="production-plan")
        assert captured.get("show_shipping_details") is True


def test_save_plan_pdf_uses_sequential_versions(app, tmp_path):
    from app.exports import services

    with app.app_context():
        app.config["PDF_OUTPUT_DIR"] = str(tmp_path / "pdfs")
        order = Order(order_id="POD-SEQ-100", customer_name="Version Customer")
        bootstrap_order_rows(order)
        db.session.add(order)
        db.session.commit()

        a1 = services.save_plan_pdf(order, b"%PDF-1.4\n%%EOF", "customer-plan", display_order_id="POD-SEQ-100")
        db.session.add(a1)
        db.session.commit()
        assert a1.filename == "customer-plan-POD-SEQ-100-V1.pdf"

        a2 = services.save_plan_pdf(order, b"%PDF-1.4\n%%EOF", "customer-plan", display_order_id="POD-SEQ-100")
        db.session.add(a2)
        db.session.commit()
        assert a2.filename == "customer-plan-POD-SEQ-100-V2.pdf"

        p1 = services.save_plan_pdf(order, b"%PDF-1.4\n%%EOF", "production-plan", display_order_id="IRA-SEQ-100")
        db.session.add(p1)
        db.session.commit()
        assert p1.filename == "production-plan-IRA-SEQ-100-V1.pdf"
