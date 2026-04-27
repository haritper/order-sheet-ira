import os
from pathlib import Path


def _default_sqlite_uri():
    db_path = (Path(__file__).resolve().parents[1] / "instance" / "ordersheet.db").resolve()
    return f"sqlite:///{db_path.as_posix()}"


def _resolve_database_uri():
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        return _default_sqlite_uri()

    # Normalize relative sqlite paths so server startup cwd does not matter.
    # Example: sqlite:///instance/ordersheet.db -> absolute sqlite URI.
    if raw.startswith("sqlite:///") and not raw.startswith("sqlite:////"):
        rel = raw.replace("sqlite:///", "", 1).replace("\\", "/").strip("/")
        if rel and not (len(rel) > 1 and rel[1] == ":"):
            abs_db = (Path(__file__).resolve().parents[1] / rel).resolve()
            return f"sqlite:///{abs_db.as_posix()}"
    return raw


class Config:
    _PROJECT_ROOT = Path(__file__).resolve().parents[1]
    _WORKSPACE_ROOT = Path(__file__).resolve().parents[2]

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = _resolve_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", str((_PROJECT_ROOT / "data" / "uploads").resolve()))
    PDF_OUTPUT_DIR = os.environ.get("PDF_OUTPUT_DIR", str((_PROJECT_ROOT / "data" / "pdfs").resolve()))
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
    S3_BUCKET = os.environ.get("S3_BUCKET", "")
    S3_PREFIX = os.environ.get("S3_PREFIX", "")
    AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", ""))
    TIMEZONE = os.environ.get("TIMEZONE", "Asia/Kolkata")
    AI_VERIFY_ENABLED = os.environ.get("AI_VERIFY_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    AI_VERIFY_MODEL = os.environ.get(
        "AI_VERIFY_MODEL",
        os.environ.get("OPENAI_ROSTER_MODEL", "gpt-4.1"),
    )
    CUTTING_PLAN_SOURCE = os.environ.get("CUTTING_PLAN_SOURCE", "deterministic")
    ORDER_DELETE_PIN = os.environ.get("ORDER_DELETE_PIN", "2019")
    ADMIN_DEFAULT_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")
    OPERATOR_DEFAULT_PASSWORD = os.environ.get("OPERATOR_PASSWORD", "Operator@123")
    INVOICE_RECEIPT_REQUIRED = (
        os.environ.get("INVOICE_RECEIPT_REQUIRED", "true").strip().lower() in {"1", "true", "yes", "on"}
    )

    PRICING_DATABASE = os.environ.get(
        "PRICING_DATABASE",
        str((_PROJECT_ROOT / "instance" / "pricing_app_v3.db").resolve()),
    )
    PRICING_UPLOAD_FOLDER = os.environ.get(
        "PRICING_UPLOAD_FOLDER",
        str((_PROJECT_ROOT / "data" / "pricing_uploads").resolve()),
    )
    PRICING_WORKBOOK_PATH = os.environ.get(
        "PRICING_WORKBOOK_PATH",
        str((_WORKSPACE_ROOT / "pricing intelligence" / "DATA SHEET_1 (Autosaved).xlsx").resolve()),
    )
    PRICING_SOURCE_CURRENCY = os.environ.get("PRICING_SOURCE_CURRENCY", "INR")
    PRICING_DISPLAY_CURRENCY = os.environ.get("PRICING_DISPLAY_CURRENCY", "USD")
    PRICING_FX_CACHE_PATH = os.environ.get(
        "PRICING_FX_CACHE_PATH",
        str((_PROJECT_ROOT / "instance" / "pricing_fx_rate_cache.json").resolve()),
    )
    PRICING_OWNER_DEFAULT_PASSWORD = os.environ.get(
        "PRICING_OWNER_PASSWORD",
        ADMIN_DEFAULT_PASSWORD,
    )
    PRICING_EMPLOYEE_DEFAULT_PASSWORD = os.environ.get(
        "PRICING_EMPLOYEE_PASSWORD",
        "ChangeMe123!",
    )
    WORK_TIMING_ALERT_MODE = os.environ.get("WORK_TIMING_ALERT_MODE", "log")
    WORK_TIMING_GIRI_CONTACT = os.environ.get("WORK_TIMING_GIRI_CONTACT", "")
    WORK_TIMING_MD_CONTACT = os.environ.get("WORK_TIMING_MD_CONTACT", "")
    WORK_TIMING_WEBHOOK_URL = os.environ.get("WORK_TIMING_WEBHOOK_URL", "")
    WORK_TIMING_WEBHOOK_TOKEN = os.environ.get("WORK_TIMING_WEBHOOK_TOKEN", "")
    try:
        WORK_TIMING_WEBHOOK_TIMEOUT_SECONDS = float(
            os.environ.get("WORK_TIMING_WEBHOOK_TIMEOUT_SECONDS", "15") or "15"
        )
    except (TypeError, ValueError):
        WORK_TIMING_WEBHOOK_TIMEOUT_SECONDS = 15.0
    CUSTOMER_PLAN_WEBHOOK_ENABLED = (
        os.environ.get("CUSTOMER_PLAN_WEBHOOK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    )
    CUSTOMER_PLAN_WEBHOOK_URL = os.environ.get("CUSTOMER_PLAN_WEBHOOK_URL", "")
    CUSTOMER_PLAN_WEBHOOK_TOKEN = os.environ.get("CUSTOMER_PLAN_WEBHOOK_TOKEN", "")
    try:
        CUSTOMER_PLAN_WEBHOOK_TIMEOUT_SECONDS = float(
            os.environ.get("CUSTOMER_PLAN_WEBHOOK_TIMEOUT_SECONDS", "15") or "15"
        )
    except (TypeError, ValueError):
        CUSTOMER_PLAN_WEBHOOK_TIMEOUT_SECONDS = 15.0
    ASSIGN_MAIL_SMTP_HOST = os.environ.get("ASSIGN_MAIL_SMTP_HOST", "")
    try:
        ASSIGN_MAIL_SMTP_PORT = int(os.environ.get("ASSIGN_MAIL_SMTP_PORT", "587") or "587")
    except (TypeError, ValueError):
        ASSIGN_MAIL_SMTP_PORT = 587
    ASSIGN_MAIL_USERNAME = os.environ.get("ASSIGN_MAIL_USERNAME", "")
    ASSIGN_MAIL_PASSWORD = os.environ.get("ASSIGN_MAIL_PASSWORD", "")
    ASSIGN_MAIL_USE_TLS = (
        os.environ.get("ASSIGN_MAIL_USE_TLS", "true").strip().lower() in {"1", "true", "yes", "on"}
    )
    ASSIGN_MAIL_USE_SSL = (
        os.environ.get("ASSIGN_MAIL_USE_SSL", "false").strip().lower() in {"1", "true", "yes", "on"}
    )
    ASSIGN_MAIL_FROM = os.environ.get("ASSIGN_MAIL_FROM", ASSIGN_MAIL_USERNAME)
    ASSIGN_MAIL_CC = os.environ.get("ASSIGN_MAIL_CC", "")
    ASSIGN_MAIL_REPLY_TO = os.environ.get("ASSIGN_MAIL_REPLY_TO", ASSIGN_MAIL_FROM)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
