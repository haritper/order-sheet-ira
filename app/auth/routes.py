from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.auth.forms import LoginForm
from app.models import User


auth_bp = Blueprint("auth", __name__)

def _find_user_by_login_username(raw_username: str) -> User | None:
    token = str(raw_username or "").strip().lower()
    if not token or "@" in token:
        return None

    users = User.query.filter_by(is_active_user=True).all()
    for user in users:
        email = str(user.email or "").strip().lower()
        local_part = email.split("@", 1)[0] if email else ""
        if token == local_part:
            return user
    return None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("orders.list_orders"))

    form = LoginForm()
    if form.validate_on_submit():
        user = _find_user_by_login_username(form.username.data)
        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("orders.list_orders"))
        flash("Invalid username or password", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
