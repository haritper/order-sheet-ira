from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin.forms import UserCreateForm
from app.extensions import db
from app.models import Order, OrderAssignment, OrderAssignmentStatus, Role, User
from app.order_numbers import build_order_code, consume_pod_numbers, get_or_create_counter_settings
from app.utils import roles_required


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/dashboard")
@login_required
@roles_required(Role.ADMIN.value)
def dashboard():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    pod_orders = [o for o in orders if str(getattr(o, "order_id", "") or "").strip().upper().startswith("POD-")]
    ira_orders = [o for o in orders if str(getattr(o, "production_order_id", "") or "").strip()]
    operators = (
        User.query.filter(
            User.is_active_user.is_(True),
            User.role.in_([Role.OPERATOR.value, Role.MANAGER.value]),
        )
        .order_by(User.full_name.asc(), User.id.asc())
        .all()
    )
    assignments = OrderAssignment.query.order_by(OrderAssignment.created_at.desc()).all()

    metrics = {
        "total_orders": len(orders),
        "total_assignments": len(assignments),
        "pod_total": len(pod_orders),
        "ira_total": len(ira_orders),
    }

    order_status_counts = {
        "DRAFT": 0,
        "READY_FOR_APPROVAL": 0,
        "APPROVED": 0,
    }
    for order in orders:
        status = str(getattr(order, "status", "") or "").strip().upper()
        if status in order_status_counts:
            order_status_counts[status] += 1

    operator_map = {}
    for operator in operators:
        operator_map[int(operator.id)] = {
            "operator_id": int(operator.id),
            "operator_name": str(operator.full_name or "").strip() or f"Operator {operator.id}",
            "assigned_total": 0,
            "assignment_pending": 0,
            "assignment_in_progress": 0,
            "assignment_completed": 0,
            "order_draft": 0,
            "order_ready": 0,
            "order_approved": 0,
            "order_unlinked": 0,
        }

    for row in assignments:
        op_id = int(getattr(row, "operator_id", 0) or 0)
        if op_id not in operator_map:
            continue
        card = operator_map[op_id]
        card["assigned_total"] += 1

        assignment_status = str(getattr(row, "status", "") or "").strip().upper()
        if assignment_status == OrderAssignmentStatus.PENDING.value:
            card["assignment_pending"] += 1
        elif assignment_status == OrderAssignmentStatus.IN_PROGRESS.value:
            card["assignment_in_progress"] += 1
        elif assignment_status == OrderAssignmentStatus.COMPLETED.value:
            card["assignment_completed"] += 1

        linked = getattr(row, "linked_order", None)
        if linked is None:
            linked = getattr(row, "order", None)
        if linked is None:
            card["order_unlinked"] += 1
            continue
        linked_status = str(getattr(linked, "status", "") or "").strip().upper()
        if linked_status == "DRAFT":
            card["order_draft"] += 1
        elif linked_status == "READY_FOR_APPROVAL":
            card["order_ready"] += 1
        elif linked_status == "APPROVED":
            card["order_approved"] += 1

    operator_cards = list(operator_map.values())
    assignment_distribution = [
        {"label": row["operator_name"], "value": int(row["assigned_total"])}
        for row in operator_cards
        if int(row["assigned_total"]) > 0
    ]
    if not assignment_distribution:
        assignment_distribution = [{"label": "No Assignments", "value": 1}]

    order_status_distribution = [
        {"label": "Draft", "value": int(order_status_counts["DRAFT"])},
        {"label": "Ready", "value": int(order_status_counts["READY_FOR_APPROVAL"])},
        {"label": "Approved", "value": int(order_status_counts["APPROVED"])},
    ]
    if sum(v["value"] for v in order_status_distribution) <= 0:
        order_status_distribution = [{"label": "No Orders", "value": 1}]

    return render_template(
        "admin/dashboard.html",
        metrics=metrics,
        operator_cards=operator_cards,
        assignment_distribution=assignment_distribution,
        order_status_distribution=order_status_distribution,
    )


@admin_bp.route("/users", methods=["GET", "POST"])
@login_required
@roles_required(Role.ADMIN.value)
def users():
    form = UserCreateForm()
    if form.validate_on_submit():
        username = str(form.username.data or "").strip().lower()
        if User.query.filter_by(email=username).first():
            flash("Username already exists.", "danger")
            return redirect(url_for("admin.users"))
        requested_role = str(form.role.data or "").strip().lower()
        if requested_role == Role.ADMIN.value:
            existing_admin = User.query.filter(User.role == Role.ADMIN.value).first()
            if existing_admin is not None:
                flash("Only one admin user is allowed.", "danger")
                return redirect(url_for("admin.users"))
        if requested_role == Role.MANAGER.value:
            existing_manager = User.query.filter(User.role == Role.MANAGER.value).first()
            if existing_manager is not None:
                flash("Only one manager user is allowed.", "danger")
                return redirect(url_for("admin.users"))

        user = User(
            full_name=username,
            email=username,
            role=requested_role,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash("User created.", "success")
        return redirect(url_for("admin.users"))

    users_list = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users_list, form=form)


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@roles_required(Role.ADMIN.value)
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    role = str(user.role or "").strip().lower()
    if role not in {Role.OPERATOR.value, Role.MANAGER.value}:
        flash("Only operator or manager users can be deleted here.", "danger")
        return redirect(url_for("admin.users"))

    linked_assignments = OrderAssignment.query.filter_by(operator_id=int(user.id)).count()
    if linked_assignments > 0:
        flash("Cannot delete operator with existing order assignments.", "danger")
        return redirect(url_for("admin.users"))

    db.session.delete(user)
    db.session.commit()
    flash(f"{role.title()} deleted.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/order-assignments", methods=["GET", "POST"])
@login_required
@roles_required(Role.ADMIN.value, Role.MANAGER.value)
def order_assignments():
    operators = (
        User.query.filter(
            User.is_active_user.is_(True),
            User.role.in_([Role.OPERATOR.value, Role.MANAGER.value]),
        )
        .order_by(User.full_name.asc(), User.id.asc())
        .all()
    )
    counters = get_or_create_counter_settings()
    db.session.commit()

    if request.method == "POST":
        allocation = []
        for operator in operators:
            count_raw = str(request.form.get(f"count_{operator.id}", "0") or "0").strip()
            if count_raw == "":
                count_raw = "0"
            if not count_raw.isdigit():
                flash(f"Invalid count for {operator.full_name}.", "danger")
                return redirect(url_for("admin.order_assignments"))
            count = int(count_raw)
            for _ in range(count):
                allocation.append(operator)

        total = len(allocation)
        if total <= 0:
            flash("Enter at least one order count to assign.", "danger")
            return redirect(url_for("admin.order_assignments"))

        team_names = []
        for idx in range(total):
            team_name = str(request.form.get(f"team_name_{idx}", "") or "").strip()
            if not team_name:
                flash(f"Team name is required for row {idx + 1}.", "danger")
                return redirect(url_for("admin.order_assignments"))
            team_names.append(team_name)

        sequence_numbers, sequence_width = consume_pod_numbers(count=total)
        codes = [build_order_code("POD", sequence_numbers[idx], sequence_width, team_names[idx]) for idx in range(total)]

        existing = {
            row.order_code
            for row in OrderAssignment.query.filter(OrderAssignment.order_code.in_(codes)).all()
        }
        if existing:
            sample = sorted(existing)[0]
            flash(
                f"Order code already exists ({sample}). Choose another start number/team name.",
                "danger",
            )
            return redirect(url_for("admin.order_assignments"))

        new_rows = []
        for idx in range(total):
            sequence_number = sequence_numbers[idx]
            assignment = OrderAssignment(
                order_code=codes[idx],
                team_name=team_names[idx],
                operator_id=allocation[idx].id,
                sequence_number=sequence_number,
                month_abbr=datetime.now().strftime("%b").upper(),
                year=datetime.now().year,
                status=OrderAssignmentStatus.PENDING.value,
            )
            new_rows.append(assignment)
            db.session.add(assignment)

        db.session.commit()
        flash(f"Created {len(new_rows)} assigned order IDs.", "success")
        return redirect(url_for("admin.order_assignments"))

    recent_assignments = OrderAssignment.query.order_by(OrderAssignment.created_at.desc()).limit(200).all()
    return render_template(
        "admin/order_assignments.html",
        operators=operators,
        recent_assignments=recent_assignments,
        counters=counters,
    )


@admin_bp.route("/order-id-counters", methods=["GET", "POST"])
@login_required
@roles_required(Role.ADMIN.value)
def order_id_counters():
    counters = get_or_create_counter_settings()
    if request.method == "POST":
        pod_raw = str(request.form.get("pod_next_number", "") or "").strip()
        ira_raw = str(request.form.get("ira_next_number", "") or "").strip()
        invoice_raw = str(
            request.form.get("invoice_next_number", getattr(counters, "invoice_next_number", 1)) or ""
        ).strip()
        if not pod_raw.isdigit() or int(pod_raw) <= 0:
            flash("POD next custom number must be a positive number.", "danger")
            return redirect(url_for("admin.order_id_counters"))
        if not ira_raw.isdigit() or int(ira_raw) <= 0:
            flash("IRA next custom number must be a positive number.", "danger")
            return redirect(url_for("admin.order_id_counters"))
        if not invoice_raw.isdigit() or int(invoice_raw) <= 0:
            flash("Invoice next custom number must be a positive number.", "danger")
            return redirect(url_for("admin.order_id_counters"))
        counters.pod_next_number = int(pod_raw)
        counters.ira_next_number = int(ira_raw)
        counters.invoice_next_number = int(invoice_raw)
        db.session.commit()
        flash("Order ID counters updated.", "success")
        return redirect(url_for("admin.order_id_counters"))

    db.session.commit()
    return render_template("admin/order_id_counters.html", counters=counters)
