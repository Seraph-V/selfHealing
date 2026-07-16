# SCENARIO: architectural_regression
# TYPE: Architectural (circular import)
# BROKEN: unused circular import between OrderService and UserService

from src.data import get_record


class OrderService:
    def get_orders_for(self, user_id: int) -> list:
        return ["order_101", "order_102"]

    def get_order_summary(self, user_id: int) -> str:
        record = get_record("users", user_id) or {}
        name = record.get("name", "Unknown")
        return f"{name}: {len(self.get_orders_for(user_id))} orders"
