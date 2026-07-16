# SCENARIO: architectural_regression_2
# TYPE: Architectural (circular import, functionally load-bearing)
# FIXED: Removed direct import of UserService and use shared data layer.
from src.data import get_record


class OrderService:
    def get_orders_for(self, user_id: int) -> list:
        return get_record("orders", user_id)

    def get_order_summary(self, user_id: int) -> str:
        name = get_record("users", user_id).get("name")
        orders = self.get_orders_for(user_id)
        return f"{name}: {len(orders)} orders"
