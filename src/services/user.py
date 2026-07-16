# SCENARIO: architectural_regression_2
# TYPE: Architectural (circular import, functionally load-bearing)
# FIXED: Removed direct import of OrderService and use shared data layer.
from src.data import get_record


class UserService:
    def get_user(self, user_id: int) -> dict:
        return get_record("users", user_id)

    def get_name(self, user_id: int) -> str:
        user = self.get_user(user_id)
        return user.get("name", "Unknown")

    def get_order_count(self, user_id: int) -> int:
        orders = get_record("orders", user_id)
        return len(orders)
