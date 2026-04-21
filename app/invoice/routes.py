from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user

from app.auth.forms import LoginForm
from app.auth.routes import _find_user_by_login_username
from app.models import Role


invoice_bp = Blueprint("invoice", __name__)


def _is_strict_admin(user) -> bool:
    if user is None:
        return False
    role = str(getattr(user, "role", "") or "").strip().lower()
    return role == Role.ADMIN.value


@invoice_bp.route("/invoice", methods=["GET", "POST"])
def invoice_login():
    if current_user.is_authenticated and _is_strict_admin(current_user):
        return redirect(url_for("invoice.invoice_preview"))

    form = LoginForm()
    if form.validate_on_submit():
        user = _find_user_by_login_username(form.username.data)
        if user and user.check_password(form.password.data):
            if _is_strict_admin(user):
                login_user(user)
                next_page = request.args.get("next")
                return redirect(next_page or url_for("invoice.invoice_preview"))
            flash("Only admin can access invoice module.", "danger")
        else:
            flash("Invalid username or password", "danger")

    return render_template("invoice/login.html", form=form)


@invoice_bp.route("/invoice/preview")
def invoice_preview():
    if not current_user.is_authenticated:
        return redirect(url_for("invoice.invoice_login", next=request.path))
    if not _is_strict_admin(current_user):
        abort(403)
    return render_template("invoice/preview.html")
