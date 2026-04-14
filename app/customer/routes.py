import uuid

from flask import Blueprint, flash, redirect, render_template, url_for

from app.customer.forms import CustomerRequestForm
from app.extensions import db
from app.models import CustomerRequest
from app.orders.services import PRODUCT_CATALOG
from app.storage import save_global_file


customer_bp = Blueprint("customer", __name__, url_prefix="/customer")


@customer_bp.route("/new", methods=["GET", "POST"])
def customer_new():
    form = CustomerRequestForm()
    form.requested_products.choices = [(p, p) for p in PRODUCT_CATALOG]

    if form.validate_on_submit():
        csv_path = None
        if form.roster_csv.data and form.roster_csv.data.filename:
            filename = f"customer-{uuid.uuid4().hex[:10]}-{form.roster_csv.data.filename}"
            csv_path = save_global_file(
                "customer_requests",
                filename,
                form.roster_csv.data.read(),
                content_type=form.roster_csv.data.content_type or "text/csv",
            )

        row = CustomerRequest(
            customer_name=form.customer_name.data,
            team_name=form.team_name.data,
            email=form.email.data,
            mobile=form.mobile.data,
            requested_products=",".join(form.requested_products.data),
            notes=form.notes.data,
            roster_csv_path=csv_path,
        )
        db.session.add(row)
        db.session.commit()
        flash("Request submitted. Our team will contact you.", "success")
        return redirect(url_for("customer.customer_new"))

    return render_template("customer/new.html", form=form)
