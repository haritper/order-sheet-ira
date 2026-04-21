import logging
import os
from pathlib import Path

from flask import Flask, jsonify, render_template
from sqlalchemy import inspect

from app.config import Config
from app.extensions import bcrypt, db, login_manager, migrate
from app.models import User
from app.pricing_integration.routes import init_pricing_module, pricing_bp


def create_app(config_object=None):
    app = Flask(__name__)

    if config_object:
        app.config.from_object(config_object)
    else:
        app.config.from_object(Config)

    _prepare_storage(app)
    _configure_native_pdf_env()
    _configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    from app import models  # noqa: F401

    from app.auth.routes import auth_bp
    from app.orders.routes import orders_bp
    from app.players.routes import players_bp
    from app.exports.routes import exports_bp
    from app.admin.routes import admin_bp
    from app.customer.routes import customer_bp
    from app.work_timing.routes import work_timing_bp
    from app.invoice.routes import invoice_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(players_bp)
    app.register_blueprint(exports_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(work_timing_bp)
    app.register_blueprint(invoice_bp)
    app.register_blueprint(pricing_bp)
    init_pricing_module(app)
    _seed_default_users(app)
    _register_cli(app)
    _register_template_context(app)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok"}), 200

    @app.route("/errors/log", methods=["POST"])
    def log_error():
        app.logger.error("Client-side error reported", extra={"event": "client_error"})
        return jsonify({"status": "logged"}), 202

    return app


def _prepare_storage(app):
    for key in ("UPLOAD_DIR", "PDF_OUTPUT_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)


def _configure_logging(app):
    if app.logger.handlers:
        return

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}'
    )
    handler.setFormatter(formatter)
    app.logger.addHandler(handler)
    app.logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def _configure_native_pdf_env():
    """Configure native dynamic library lookup paths for WeasyPrint on macOS/Homebrew."""
    candidates = [
        "/opt/homebrew/lib",
        "/opt/homebrew/opt/glib/lib",
        "/opt/homebrew/opt/pango/lib",
        "/opt/homebrew/opt/harfbuzz/lib",
        "/opt/homebrew/opt/gdk-pixbuf/lib",
        "/opt/homebrew/opt/cairo/lib",
    ]
    local_weasy_dir = Path.cwd() / ".weasy-libs"
    local_weasy_dir.mkdir(parents=True, exist_ok=True)
    _ensure_weasy_symlinks(local_weasy_dir)
    candidates.insert(0, str(local_weasy_dir))

    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    existing_parts = [p for p in existing.split(":") if p]
    merged = []
    for path in candidates + existing_parts:
        if path and path not in merged:
            merged.append(path)
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(merged)


def _ensure_weasy_symlinks(base_dir: Path):
    links = {
        "libgobject-2.0-0": "/opt/homebrew/opt/glib/lib/libgobject-2.0.dylib",
        "libglib-2.0-0": "/opt/homebrew/opt/glib/lib/libglib-2.0.dylib",
        "libpango-1.0-0": "/opt/homebrew/opt/pango/lib/libpango-1.0.dylib",
        "libpangocairo-1.0-0": "/opt/homebrew/opt/pango/lib/libpangocairo-1.0.dylib",
        "libpangoft2-1.0-0": "/opt/homebrew/opt/pango/lib/libpangoft2-1.0.dylib",
        "libharfbuzz-0": "/opt/homebrew/opt/harfbuzz/lib/libharfbuzz.dylib",
        "libharfbuzz-subset-0": "/opt/homebrew/opt/harfbuzz/lib/libharfbuzz-subset.dylib",
        "libgdk_pixbuf-2.0-0": "/opt/homebrew/opt/gdk-pixbuf/lib/libgdk_pixbuf-2.0.dylib",
    }
    for link_name, target in links.items():
        target_path = Path(target)
        if not target_path.exists():
            continue
        link_path = base_dir / link_name
        try:
            if link_path.is_symlink() or link_path.exists():
                if link_path.resolve() == target_path.resolve():
                    continue
                link_path.unlink()
            link_path.symlink_to(target_path)
        except OSError:
            # Non-fatal: fallback env might still resolve native libs.
            continue


def _register_cli(app):
    @app.cli.command("create-admin")
    def create_admin():
        from app.extensions import db
        from app.models import User

        email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
        password = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")
        full_name = os.environ.get("ADMIN_NAME", "Admin User")

        if User.query.filter_by(email=email).first():
            print(f"Admin user already exists: {email}")
            return

        user = User(email=email, full_name=full_name, role="admin")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Created admin user: {email}")

    @app.cli.command("seed-default-users")
    def seed_default_users():
        _seed_default_users(app)
        print("Seeded default admin and operator users.")

    @app.cli.command("work-timing-check")
    def work_timing_check():
        from app.work_timing.services import run_work_timing_overdue_check

        summary = run_work_timing_overdue_check()
        print(
            "work_timing_check scanned={scanned} giri_sent={giri_sent} md_sent={md_sent}".format(
                scanned=int(summary.get("scanned", 0)),
                giri_sent=int(summary.get("giri_sent", 0)),
                md_sent=int(summary.get("md_sent", 0)),
            )
        )


def _register_template_context(app):
    @app.context_processor
    def inject_branding():
        return {"brand_logo_filename": _resolve_brand_logo_filename(app)}


def _resolve_brand_logo_filename(app):
    static_dir = Path(app.static_folder or "")
    preferred = ["ira-brand-logo.png", "ira-logo-new.png", "ira-logo.png"]
    for name in preferred:
        if (static_dir / "img" / name).exists():
            return f"img/{name}"
    return "img/ira-logo.png"


def _seed_default_users(app):
    with app.app_context():
        inspector = inspect(db.engine)
        if not inspector.has_table("users"):
            return

        operator_password = app.config["OPERATOR_DEFAULT_PASSWORD"]
        admin_password = app.config["ADMIN_DEFAULT_PASSWORD"]
        defaults = [
            ("giri@gmail.com", "Giri", "operator", admin_password),
            ("subash@gmail.com", "Subash", "operator", operator_password),
            ("sudharshan@gmail.com", "Sudharshan", "operator", operator_password),
            ("admin@example.com", "Admin User", "admin", admin_password),
        ]

        changed = False
        for email, full_name, role, password in defaults:
            user = User.query.filter_by(email=email).first()
            if user is None:
                user = User(email=email, full_name=full_name, role=role)
                user.set_password(password)
                db.session.add(user)
                changed = True
                continue

            needs_update = False
            if user.full_name != full_name:
                user.full_name = full_name
                needs_update = True
            if user.role != role:
                user.role = role
                needs_update = True
            if not user.check_password(password):
                user.set_password(password)
                needs_update = True
            if needs_update:
                changed = True

        if changed:
            db.session.commit()
