from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

SIZE_CHOICES = [
    ("XS", "XS"),
    ("S", "S"),
    ("M", "M"),
    ("L", "L"),
    ("XL", "XL"),
    ("2XL", "2XL"),
    ("3XL", "3XL"),
    ("4XL", "4XL"),
]

SLEEVE_CHOICES = [("HALF", "HALF"), ("FULL", "FULL"), ("3/4 TH", "3/4 TH")]


class OrderHeaderForm(FlaskForm):
    order_id = StringField("Order ID", validators=[DataRequired(), Length(max=64)])
    enquiry_date = DateField("Enquiry Date", validators=[Optional()])
    submission_id = StringField("Submission ID", validators=[Optional(), Length(max=64)])
    confirmed_on = DateField("Confirmed On", validators=[Optional()])

    customer_name = StringField("Customer Name", validators=[DataRequired(), Length(max=255)])
    mobile = StringField("Mobile", validators=[Optional(), Length(max=40)])
    shipping_address = StringField(
        "Shipping Address", validators=[Optional(), Length(max=512)]
    )
    city = StringField("City", validators=[Optional(), Length(max=120)])
    zip_code = StringField("ZIP Code", validators=[Optional(), Length(max=20)])
    state = StringField("State", validators=[Optional(), Length(max=80)])
    country = StringField("Country", validators=[Optional(), Length(max=80)])

    submit = SubmitField("Save Step 1")


class OrderItemForm(FlaskForm):
    product_name = StringField("Product", validators=[DataRequired(), Length(max=100)])
    sleeve_type = SelectField("Sleeve Type", choices=SLEEVE_CHOICES, validators=[Optional()])
    gender = StringField("Gender", validators=[Optional(), Length(max=20)], default="MENS")

    qty_xs = IntegerField("XS", validators=[Optional(), NumberRange(min=0)], default=0)
    qty_s = IntegerField("S", validators=[Optional(), NumberRange(min=0)], default=0)
    qty_m = IntegerField("M", validators=[Optional(), NumberRange(min=0)], default=0)
    qty_l = IntegerField("L", validators=[Optional(), NumberRange(min=0)], default=0)
    qty_xl = IntegerField("XL", validators=[Optional(), NumberRange(min=0)], default=0)
    qty_2xl = IntegerField("2XL", validators=[Optional(), NumberRange(min=0)], default=0)
    qty_3xl = IntegerField("3XL", validators=[Optional(), NumberRange(min=0)], default=0)
    qty_4xl = IntegerField("4XL", validators=[Optional(), NumberRange(min=0)], default=0)


class AccessoryForm(FlaskForm):
    product_name = StringField("Accessory", validators=[DataRequired(), Length(max=100)])
    quantity = IntegerField("Quantity", validators=[DataRequired(), NumberRange(min=0)])
    color = StringField("Color", validators=[Optional(), Length(max=80)])
    logo_type = StringField("Logo Type", validators=[Optional(), Length(max=80)])
    fabric = StringField("Fabric", validators=[Optional(), Length(max=80)])


class BrandingSpecForm(FlaskForm):
    garment_type = StringField("Garment Type", validators=[DataRequired(), Length(max=60)])
    style_number = StringField("Style #", validators=[Optional(), Length(max=60)])
    collar_type = StringField("Collar Type", validators=[Optional(), Length(max=80)])
    fabric = StringField("Fabric", validators=[Optional(), Length(max=120)])
    panel_color_primary = StringField("Primary Color", validators=[Optional(), Length(max=80)])
    panel_color_secondary = StringField(
        "Secondary Color", validators=[Optional(), Length(max=80)]
    )

    right_chest_logo = StringField("Right Chest Logo", validators=[Optional(), Length(max=120)])
    left_chest_logo = StringField("Left Chest Logo", validators=[Optional(), Length(max=120)])
    right_sleeve_logo = StringField("Right Sleeve Logo", validators=[Optional(), Length(max=120)])
    back_logo = StringField("Back Logo", validators=[Optional(), Length(max=120)])
    left_sleeve_logo = StringField("Left Sleeve Logo", validators=[Optional(), Length(max=120)])

    design_notes = TextAreaField("Design Notes", validators=[Optional()])
    production_notes = TextAreaField("Production Notes", validators=[Optional()])


class Step2Form(FlaskForm):
    submit = SubmitField("Save Step 2")


class Step4ApprovalForm(FlaskForm):
    checklist_images_verified = BooleanField("I checked all information and images for accuracy")
    checklist_color_variance = BooleanField("I understand render and color variance notes")
    checklist_lead_time = BooleanField("I understand lead time and delay policy")
    checklist_add_on_policy = BooleanField("I understand add-on order policy")
    approval_notes = TextAreaField("Approval Notes", validators=[Optional()])
    submit_ready = SubmitField("Mark Ready for Approval")
    submit_approve = SubmitField("Approve Order")
