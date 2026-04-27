from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Regexp


class UserCreateForm(FlaskForm):
    username = StringField(
        "Username",
        validators=[
            DataRequired(),
            Length(max=255),
            Regexp(
                r"^[A-Za-z0-9._-]+$",
                message="Username can include letters, numbers, dot, underscore, and hyphen only.",
            ),
        ],
    )
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    role = SelectField("Role", choices=[("operator", "operator"), ("manager", "manager"), ("admin", "admin")])
    submit = SubmitField("Create User")
