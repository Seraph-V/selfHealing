# SCENARIO: architectural_regression_2
# TYPE: Architectural (circular import, functionally load-bearing)
# BROKEN: get_order_count() wrongly imports OrderService directly instead
# of reading through the shared src.data layer, causing a circular import.
from src.services.order import OrderService


class UserService:
    def get_user(self, user_id: int) -> dict:
        return {"id": 1, "name": "Alice"}

    def get_name(self, user_id: int) -> str:
        user = self.get_user(user_id)
        return user.get("name", "Unknown")

    def get_order_count(self, user_id: int) -> int:
        return len(OrderService().get_orders_for(user_id))
