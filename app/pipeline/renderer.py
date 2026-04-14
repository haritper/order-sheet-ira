from app.pipeline.config import PACKING_SECTION_ORDER


def render_final_output(outputs):
    status = outputs.get("approval_decision", {}).get("status", "REJECTED")

    if status == "REJECTED":
        return {
            "status": status,
            "message": "Production tables not rendered because approval is rejected.",
            "exceptions": outputs.get("validation_report", {}),
        }

    packing = outputs.get("packing_list", {})
    overview = outputs.get("order_overview", {})

    rendered = {
        "status": status,
        "order_overview_render_order": [
            "MEN’S UNIFORMS",
            "WOMEN’S UNIFORMS",
            "KID’S / YOUTH UNIFORMS",
            "ACCESORIES",
        ],
        "packing_render_order": [
            "GENDER MENS SLEEVE TYPE HALF",
            "GENDER MENS SLEEVE TYPE FULL",
            "GENDER WOMENS SLEEVE TYPE HALF",
            "GENDER WOMENS SLEEVE TYPE FULL",
            "GENDER YOUTH SLEEVE TYPE HALF",
            "GENDER YOUTH SLEEVE TYPE FULL",
        ],
        "order_overview": overview,
        "packing_list": {section: packing.get(section, []) for section in PACKING_SECTION_ORDER},
    }

    if status == "NEEDS_MANUAL_REVIEW":
        rendered["exceptions"] = outputs.get("validation_report", {})

    return rendered
