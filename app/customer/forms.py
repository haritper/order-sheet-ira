from flask_wtf import FlaskForm
from wtforms import EmailField, FileField, SelectMultipleField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class CustomerRequestForm(FlaskForm):
    customer_name = StringField("Customer Name", validators=[DataRequired(), Length(max=255)])
    team_name = StringField("Team Name", validators=[Optional(), Length(max=255)])
    email = EmailField("Email", validators=[Optional(), Length(max=255)])
    mobile = StringField("Mobile", validators=[Optional(), Length(max=40)])
    requested_products = SelectMultipleField(
        "Products",
        choices=[],
        validators=[DataRequired()],
    )
    roster_csv = FileField("Roster CSV")
    notes = TextAreaField("Notes")
    submit = SubmitField("Submit Request")
