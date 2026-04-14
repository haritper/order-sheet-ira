from datetime import datetime

from app.extensions import db
from app.models import Order, OrderAssignment, OrderNumberCounter
from app.order_numbers import consume_pod_numbers, get_or_assign_ira_order_id


def test_consume_pod_numbers_skips_existing_custom_numbers(app):
    now = datetime.now()
    with app.app_context():
        db.session.add(
            OrderAssignment(
                order_code="POD-USED-001",
                team_name="A",
                operator_id=2,
                sequence_number=1,
                month_abbr=now.strftime("%b").upper(),
                year=now.year,
                status="PENDING",
            )
        )
        db.session.add(
            OrderAssignment(
                order_code="POD-USED-002",
                team_name="B",
                operator_id=2,
                sequence_number=2,
                month_abbr=now.strftime("%b").upper(),
                year=now.year,
                status="PENDING",
            )
        )
        db.session.add(OrderNumberCounter(pod_next_number=1, ira_next_number=1, sequence_width=3))
        db.session.commit()

        picked, _width = consume_pod_numbers(count=2)
        db.session.commit()

        settings = OrderNumberCounter.query.first()
        assert picked == [3, 4]
        assert settings is not None
        assert settings.pod_next_number == 5


def test_get_or_assign_ira_order_id_skips_used_sequence_even_if_team_differs(app):
    now = datetime.now()
    with app.app_context():
        existing = Order(order_id="POD-EXISTING", customer_name="Existing", production_order_id="")
        db.session.add(existing)
        db.session.flush()
        existing.production_order_id = (
            f"IRA-{now.year}-{now.strftime('%b').upper()}-001-TEAM-OLD"
        )

        target = Order(order_id="POD-TARGET", customer_name="Target")
        db.session.add(target)
        db.session.add(OrderNumberCounter(pod_next_number=1, ira_next_number=1, sequence_width=3))
        db.session.commit()

        ira_id = get_or_assign_ira_order_id(target)
        db.session.commit()

        assert ira_id.startswith(f"IRA-{now.year}-{now.strftime('%b').upper()}-002-")

