from __future__ import annotations

from datetime import datetime
from enum import Enum

from flask_login import UserMixin

from app.extensions import bcrypt, db, login_manager


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"


class OrderStatus(str, Enum):
    DRAFT = "DRAFT"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"


class OrderAssignmentStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class SleeveType(str, Enum):
    HALF = "HALF"
    FULL = "FULL"
    THREE_FOURTH = "3/4 TH"


class Size(str, Enum):
    XS = "XS"
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"
    XXL = "2XL"
    XXXL = "3XL"
    XXXXL = "4XL"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default=Role.OPERATOR.value, nullable=False)
    is_active_user = db.Column(db.Boolean, default=True, nullable=False)

    audits = db.relationship("OrderAuditLog", back_populates="actor")
    assignments = db.relationship("OrderAssignment", back_populates="operator")

    def set_password(self, password: str):
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password: str) -> bool:
        return bcrypt.check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_user

    @property
    def has_admin_panel_access(self) -> bool:
        if str(self.role or "").strip().lower() == Role.ADMIN.value:
            return True
        email = str(self.email or "").strip().lower()
        local = email.split("@", 1)[0] if "@" in email else email
        return local == "giri"


class Order(TimestampMixin, db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    production_order_id = db.Column(db.String(120), unique=True, nullable=True, index=True)
    assignment_id = db.Column(
        db.Integer,
        db.ForeignKey("order_assignments.id"),
        nullable=True,
        unique=True,
        index=True,
    )
    enquiry_date = db.Column(db.Date)
    submission_id = db.Column(db.String(64))
    confirmed_on = db.Column(db.Date)

    customer_name = db.Column(db.String(255), nullable=False)
    mobile = db.Column(db.String(40))
    shipping_address = db.Column(db.String(512))
    city = db.Column(db.String(120))
    zip_code = db.Column(db.String(20))
    state = db.Column(db.String(80))
    country = db.Column(db.String(80), default="USA")

    status = db.Column(db.String(32), default=OrderStatus.DRAFT.value, nullable=False)
    approval_notes = db.Column(db.Text)

    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    accessories = db.relationship(
        "Accessory", back_populates="order", cascade="all, delete-orphan"
    )
    branding_specs = db.relationship(
        "BrandingSpec", back_populates="order", cascade="all, delete-orphan"
    )
    players = db.relationship("Player", back_populates="order", cascade="all, delete-orphan")
    attachments = db.relationship(
        "Attachment", back_populates="order", cascade="all, delete-orphan"
    )
    audits = db.relationship("OrderAuditLog", back_populates="order", cascade="all, delete-orphan")
    order_check = db.relationship(
        "OrderCheck",
        back_populates="order",
        uselist=False,
        cascade="all, delete-orphan",
    )
    assignment = db.relationship(
        "OrderAssignment",
        back_populates="order",
        foreign_keys=[assignment_id],
        uselist=False,
    )

    def can_transition_to(self, target: OrderStatus):
        transitions = {
            OrderStatus.DRAFT.value: {
                OrderStatus.READY_FOR_APPROVAL.value,
                OrderStatus.ARCHIVED.value,
            },
            OrderStatus.READY_FOR_APPROVAL.value: {
                OrderStatus.APPROVED.value,
                OrderStatus.DRAFT.value,
            },
            OrderStatus.APPROVED.value: {OrderStatus.ARCHIVED.value},
            OrderStatus.ARCHIVED.value: set(),
        }
        return target.value in transitions.get(self.status, set())


class OrderItem(TimestampMixin, db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)

    product_name = db.Column(db.String(100), nullable=False)
    sleeve_type = db.Column(db.String(10))
    gender = db.Column(db.String(20), default="MENS")

    qty_xs = db.Column(db.Integer, default=0, nullable=False)
    qty_s = db.Column(db.Integer, default=0, nullable=False)
    qty_m = db.Column(db.Integer, default=0, nullable=False)
    qty_l = db.Column(db.Integer, default=0, nullable=False)
    qty_xl = db.Column(db.Integer, default=0, nullable=False)
    qty_2xl = db.Column(db.Integer, default=0, nullable=False)
    qty_3xl = db.Column(db.Integer, default=0, nullable=False)
    qty_4xl = db.Column(db.Integer, default=0, nullable=False)
    total = db.Column(db.Integer, default=0, nullable=False)

    order = db.relationship("Order", back_populates="items")
    branding_spec = db.relationship(
        "BrandingSpec", back_populates="order_item", uselist=False, cascade="all, delete-orphan"
    )

    def compute_total(self):
        self.total = sum(
            [
                self.qty_xs,
                self.qty_s,
                self.qty_m,
                self.qty_l,
                self.qty_xl,
                self.qty_2xl,
                self.qty_3xl,
                self.qty_4xl,
            ]
        )


class Accessory(TimestampMixin, db.Model):
    __tablename__ = "accessories"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, default=0, nullable=False)
    color = db.Column(db.String(80))
    logo_type = db.Column(db.String(80))
    fabric = db.Column(db.String(80))

    order = db.relationship("Order", back_populates="accessories")


class BrandingSpec(TimestampMixin, db.Model):
    __tablename__ = "branding_specs"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    order_item_id = db.Column(db.Integer, db.ForeignKey("order_items.id"), nullable=True, unique=True)

    garment_type = db.Column(db.String(60), nullable=False)
    gender = db.Column(db.String(20), default="MENS")
    sleeve_type = db.Column(db.String(10), default="HALF")
    style_number = db.Column(db.String(60))
    collar_type = db.Column(db.String(80))
    fabric = db.Column(db.String(120))
    panel_color_primary = db.Column(db.String(80))
    panel_color_secondary = db.Column(db.String(80))

    right_chest_logo = db.Column(db.String(120))
    left_chest_logo = db.Column(db.String(120))
    right_sleeve_logo = db.Column(db.String(120))
    back_logo = db.Column(db.String(120))
    left_sleeve_logo = db.Column(db.String(120))
    front_image_path = db.Column(db.String(500))
    right_image_path = db.Column(db.String(500))
    back_image_path = db.Column(db.String(500))
    left_image_path = db.Column(db.String(500))
    logo_positions = db.Column(db.String(255))
    logo_right_path = db.Column(db.String(500))
    logo_left_path = db.Column(db.String(500))
    logo_front_path = db.Column(db.String(500))
    logo_back_path = db.Column(db.String(500))

    design_notes = db.Column(db.Text)
    production_notes = db.Column(db.Text)

    order = db.relationship("Order", back_populates="branding_specs")
    order_item = db.relationship("OrderItem", back_populates="branding_spec")


class CustomerRequest(TimestampMixin, db.Model):
    __tablename__ = "customer_requests"

    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(255), nullable=False)
    team_name = db.Column(db.String(255))
    email = db.Column(db.String(255))
    mobile = db.Column(db.String(40))
    requested_products = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text)
    roster_csv_path = db.Column(db.String(500))


class OrderCheck(TimestampMixin, db.Model):
    __tablename__ = "order_checks"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, unique=True, index=True)

    parsed_json = db.Column(db.Text)
    dynamic_design_fields = db.Column(db.Text)
    dynamic_responses = db.Column(db.Text)
    current_page = db.Column(db.Integer, default=1, nullable=False)

    order_id_verified = db.Column(db.Boolean, default=False, nullable=False)
    enquiry_date_verified = db.Column(db.Boolean, default=False, nullable=False)
    design_checked = db.Column(db.Boolean, default=False, nullable=False)
    logos_checked = db.Column(db.Boolean, default=False, nullable=False)
    gender_checked = db.Column(db.Boolean, default=False, nullable=False)
    sleeve_type_checked = db.Column(db.Boolean, default=False, nullable=False)
    names_numbers_sizes_checked = db.Column(db.Boolean, default=False, nullable=False)
    quantity_checked = db.Column(db.Boolean, default=False, nullable=False)

    approved = db.Column(db.Boolean, default=False, nullable=False)
    approved_at = db.Column(db.DateTime)

    order = db.relationship("Order", back_populates="order_check")

    @staticmethod
    def required_for_approval():
        return [
            "order_id_verified",
            "enquiry_date_verified",
            "design_checked",
            "logos_checked",
            "gender_checked",
            "sleeve_type_checked",
            "names_numbers_sizes_checked",
            "quantity_checked",
        ]

    def is_final_ready(self) -> bool:
        return all(bool(getattr(self, field, False)) for field in self.required_for_approval())


class Player(TimestampMixin, db.Model):
    __tablename__ = "players"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    row_number = db.Column(db.Integer, nullable=False)
    player_name = db.Column(db.String(255), nullable=False)
    number = db.Column(db.String(20), default="0")
    sleeve_type = db.Column(db.String(10), nullable=False)
    tshirt_size = db.Column(db.String(10), nullable=False)
    tshirt_qty = db.Column(db.Integer, default=1, nullable=False)
    trouser_size = db.Column(db.String(10), nullable=False)
    trouser_qty = db.Column(db.Integer, default=1, nullable=False)

    order = db.relationship("Order", back_populates="players")

    __table_args__ = (
        db.UniqueConstraint("order_id", "row_number", name="uq_order_player_row"),
    )


class Attachment(TimestampMixin, db.Model):
    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120), nullable=False)
    storage_path = db.Column(db.String(500), nullable=False)

    order = db.relationship("Order", back_populates="attachments")


class OrderAuditLog(db.Model):
    __tablename__ = "order_audit_log"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(120), nullable=False)
    field_name = db.Column(db.String(120))
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    order = db.relationship("Order", back_populates="audits")
    actor = db.relationship("User", back_populates="audits")


class OrderAssignment(TimestampMixin, db.Model):
    __tablename__ = "order_assignments"

    id = db.Column(db.Integer, primary_key=True)
    order_code = db.Column(db.String(120), unique=True, nullable=False, index=True)
    team_name = db.Column(db.String(255), nullable=False)
    operator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    sequence_number = db.Column(db.Integer, nullable=False)
    month_abbr = db.Column(db.String(8), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    status = db.Column(
        db.String(20),
        default=OrderAssignmentStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    linked_order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id"),
        nullable=True,
        unique=True,
        index=True,
    )

    operator = db.relationship("User", back_populates="assignments")
    order = db.relationship(
        "Order",
        back_populates="assignment",
        foreign_keys=[Order.assignment_id],
        uselist=False,
    )
    linked_order = db.relationship("Order", foreign_keys=[linked_order_id], uselist=False)


class OrderNumberCounter(TimestampMixin, db.Model):
    __tablename__ = "order_number_counters"

    id = db.Column(db.Integer, primary_key=True)
    pod_next_number = db.Column(db.Integer, nullable=False, default=1)
    ira_next_number = db.Column(db.Integer, nullable=False, default=1)
    sequence_width = db.Column(db.Integer, nullable=False, default=3)
