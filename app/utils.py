from functools import wraps

from flask import abort
from flask_login import current_user

from app.extensions import db
from app.models import OrderAuditLog


def roles_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            user_role = str(getattr(current_user, "role", "") or "").strip().lower()
            requested = {str(v or "").strip().lower() for v in roles}
            if user_role not in requested:
                abort(403)
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def add_audit(order_id, actor_id, action, field_name=None, old_value=None, new_value=None):
    log = OrderAuditLog(
        order_id=order_id,
        actor_id=actor_id,
        action=action,
        field_name=field_name,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
    )
    db.session.add(log)
