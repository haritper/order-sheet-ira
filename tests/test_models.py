from app.models import Order, OrderStatus
from app.orders.services import move_status


from app.extensions import db


def test_order_status_transition(sample_order, app):
    with app.app_context():
        order = db.session.get(Order, sample_order)
        assert order.can_transition_to(OrderStatus.READY_FOR_APPROVAL)
        move_status(order, OrderStatus.READY_FOR_APPROVAL)
        assert order.status == OrderStatus.READY_FOR_APPROVAL.value
