from app.extensions import db
from app.models import Order, OrderAuditLog, Role


def can_user_access_order(user, order: Order) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if str(getattr(user, "role", "")).strip().lower() == Role.ADMIN.value:
        return True

    assignment = getattr(order, "assignment", None)
    if assignment is not None:
        return int(getattr(assignment, "operator_id", 0) or 0) == int(user.id)

    # Transitional fallback: operator can open unassigned drafts that they created.
    row = (
        db.session.query(OrderAuditLog.id)
        .filter(
            OrderAuditLog.order_id == int(order.id),
            OrderAuditLog.actor_id == int(user.id),
            OrderAuditLog.action == "CREATE_ORDER",
        )
        .first()
    )
    return row is not None
