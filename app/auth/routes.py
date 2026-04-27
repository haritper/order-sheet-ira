from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from urllib.parse import urlparse

from app.auth.forms import LoginForm
from app.models import User


auth_bp = Blueprint("auth", __name__)

def _find_user_by_login_username(raw_username: str) -> User | None:
    token = str(raw_username or "").strip().lower()
    if not token:
        return None

    # Username-only login: users.email column stores username token.
    return User.query.filter_by(email=token, is_active_user=True).first()


def _is_safe_next_url(next_url: str | None) -> bool:
    token = str(next_url or "").strip()
    if not token:
        return False
    parsed = urlparse(token)
    # Allow only local absolute paths, block schema/host and protocol-relative URLs.
    return (
        parsed.scheme == ""
        and parsed.netloc == ""
        and token.startswith("/")
        and not token.startswith("//")
    )


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
            return redirect(next_page if _is_safe_next_url(next_page) else url_for("orders.list_orders"))
        flash("Invalid username or password", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
