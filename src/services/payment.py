# SCENARIO: architectural_regression_3
# TYPE: Architectural (three-file circular import chain, load-bearing)
# BROKEN: get_order_count_for_payment() wrongly imports OrderService
# directly instead of reading through the shared src.data layer, closing
# a three-file cycle: user -> payment -> order -> user.
from src.services.order import OrderService
from src.data import get_record


class PaymentService:
    def get_payment_status(self, user_id: int) -> str:
        record = get_record("payments", user_id) or {}
        return record.get("status", "none")

    def get_order_count_for_payment(self, user_id: int) -> int:
        return len(OrderService().get_orders_for(user_id))
