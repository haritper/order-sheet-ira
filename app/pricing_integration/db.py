from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from flask import current_app, g
from werkzeug.security import generate_password_hash


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner', 'employee')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pricing_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL UNIQUE,
    product_code TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    category TEXT NOT NULL,
    product_name TEXT,
    descriptor TEXT,
    variant TEXT,
    unique_id TEXT,
    fabric_code TEXT,
    fabric_cost REAL NOT NULL DEFAULT 0,
    printing_cost REAL NOT NULL DEFAULT 0,
    component_c REAL NOT NULL DEFAULT 0,
    component_t REAL NOT NULL DEFAULT 0,
    machine_cost REAL NOT NULL DEFAULT 0,
    accessory_total REAL NOT NULL DEFAULT 0,
    calculated_unit_rate REAL NOT NULL DEFAULT 0,
    override_unit_rate REAL,
    accessory_breakdown_json TEXT NOT NULL DEFAULT '[]',
    labor_breakdown_json TEXT NOT NULL DEFAULT '[]',
    source_row_start INTEGER,
    source_row_end INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS shipping_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_name TEXT NOT NULL UNIQUE,
    destination_country TEXT,
    fee REAL NOT NULL DEFAULT 0,
    duty_percent REAL NOT NULL DEFAULT 0,
    duty_flat REAL NOT NULL DEFAULT 0,
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT NOT NULL UNIQUE,
    external_order_id TEXT UNIQUE,
    customer_name TEXT,
    enquiry_date TEXT,
    mobile TEXT,
    shipping_address TEXT,
    destination_city TEXT,
    destination_state TEXT,
    destination_country TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    uploaded_filename TEXT NOT NULL,
    extracted_payload_json TEXT NOT NULL DEFAULT '{}',
    suggested_subtotal REAL NOT NULL DEFAULT 0,
    quoted_subtotal REAL NOT NULL DEFAULT 0,
    shipping_fee REAL NOT NULL DEFAULT 0,
    duty_fee REAL NOT NULL DEFAULT 0,
    final_landed_cost REAL NOT NULL DEFAULT 0,
    final_margin REAL NOT NULL DEFAULT 0,
    created_by_user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TEXT,
    notes TEXT,
    FOREIGN KEY (created_by_user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    product_code TEXT,
    category TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    sizes_json TEXT NOT NULL DEFAULT '{}',
    extracted_attributes_json TEXT NOT NULL DEFAULT '{}',
    unit_suggested_rate REAL NOT NULL DEFAULT 0,
    unit_quoted_rate REAL,
    line_suggested_total REAL NOT NULL DEFAULT 0,
    line_quoted_total REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE
);
"""


def get_db() -> sqlite3.Connection:
    if "pricing_db" not in g:
        database_path = Path(current_app.config["PRICING_DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        g.pricing_db = connection
    return g.pricing_db


def close_db(_: Any = None) -> None:
    connection = g.pop("pricing_db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    db = get_db()
    db.executescript(SCHEMA)
    _ensure_schema_compatibility(db)
    db.commit()
    seed_users()
    seed_shipping_profiles()


def seed_users() -> None:
    db = get_db()
    manager_password = current_app.config["PRICING_EMPLOYEE_DEFAULT_PASSWORD"]
    for attempt in range(4):
        try:
            db.executemany(
                """
                INSERT OR IGNORE INTO users (username, password_hash, role)
                VALUES (?, ?, ?)
                """,
                [
                    (
                        "admin",
                        generate_password_hash(
                            current_app.config["PRICING_OWNER_DEFAULT_PASSWORD"],
                            method="pbkdf2:sha256",
                        ),
                        "owner",
                    ),
                    (
                        "manager",
                        generate_password_hash(
                            manager_password,
                            method="pbkdf2:sha256",
                        ),
                        "employee",
                    ),
                ],
            )
            db.execute(
                """
                UPDATE users
                SET password_hash = ?, role = 'owner'
                WHERE username = 'admin'
                """,
                (
                    generate_password_hash(
                        current_app.config["PRICING_OWNER_DEFAULT_PASSWORD"],
                        method="pbkdf2:sha256",
                    ),
                ),
            )
            db.execute(
                """
                UPDATE users
                SET password_hash = ?, role = 'employee'
                WHERE username = 'manager'
                """,
                (
                    generate_password_hash(
                        manager_password,
                        method="pbkdf2:sha256",
                    ),
                ),
            )
            manager_row = db.execute(
                "SELECT id FROM users WHERE username = 'manager' LIMIT 1"
            ).fetchone()
            admin_row = db.execute(
                "SELECT id FROM users WHERE username = 'admin' LIMIT 1"
            ).fetchone()
            if manager_row and admin_row:
                db.execute(
                    """
                    UPDATE orders
                    SET created_by_user_id = ?
                    WHERE created_by_user_id NOT IN (?, ?)
                    """,
                    (manager_row["id"], manager_row["id"], admin_row["id"]),
                )
                db.execute(
                    """
                    DELETE FROM users
                    WHERE username NOT IN ('manager', 'admin')
                    """
                )
            db.commit()
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 3:
                raise
            db.rollback()
            time.sleep(0.2 * (attempt + 1))


def seed_shipping_profiles() -> None:
    db = get_db()
    existing = db.execute(
        "SELECT COUNT(*) AS count FROM shipping_profiles"
    ).fetchone()["count"]
    if existing:
        return
    db.executemany(
        """
        INSERT INTO shipping_profiles (
            profile_name, destination_country, fee, duty_percent, duty_flat, notes
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("US Standard", "USA", 0, 0, 0, "Default baseline profile."),
            ("Canada Standard", "Canada", 0, 0, 0, "Update when actuals are known."),
        ],
    )
    db.commit()


def execute(query: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
    db = get_db()
    cursor = db.execute(query, params)
    db.commit()
    return cursor


def execute_many(query: str, rows: list[tuple[Any, ...]]) -> None:
    db = get_db()
    db.executemany(query, rows)
    db.commit()


def serialize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def parse_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def init_app(app) -> None:
    app.teardown_appcontext(close_db)


def _ensure_schema_compatibility(db: sqlite3.Connection) -> None:
    columns = db.execute("PRAGMA table_info(orders)").fetchall()
    column_names = {str(column["name"]).lower() for column in columns}
    if "external_order_id" not in column_names:
        db.execute("ALTER TABLE orders ADD COLUMN external_order_id TEXT")

    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_external_order_id
        ON orders(external_order_id)
        WHERE external_order_id IS NOT NULL
        """
    )

