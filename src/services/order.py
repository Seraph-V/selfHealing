# SCENARIO: architectural_regression_2
# TYPE: Architectural (circular import, functionally load-bearing)
# BROKEN: get_order_summary() wrongly imports UserService directly instead
# of reading through the shared src.data layer, causing a circular import.
from src.data import get_record


class OrderService:
    def get_orders_for(self, user_id: int) -> list:
        return get_record("orders", user_id)

    def get_order_summary(self, user_id: int) -> str:
        name = get_record("users", user_id).get("name")
        orders = self.get_orders_for(user_id)
        return f"{name}: {len(orders)} orders"
