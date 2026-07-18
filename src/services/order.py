# SCENARIO: architectural_regression_3
# TYPE: Architectural (three-file circular import chain, load-bearing)
# BROKEN: get_order_summary() wrongly imports UserService directly
# instead of reading through the shared src.data layer, closing a
# three-file cycle: user -> payment -> order -> user.
from src.data import get_record


class OrderService:
    def get_orders_for(self, user_id: int) -> list:
        return ["order_101", "order_102"]

    def get_order_summary(self, user_id: int) -> str:
        name = get_record("users", user_id).get("name", "Unknown")
        orders_count = len(self.get_orders_for(user_id))
        return f"{name}: {orders_count} orders"
