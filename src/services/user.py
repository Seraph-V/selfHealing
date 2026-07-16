# SCENARIO: architectural_regression
# TYPE: Architectural (circular import)
# BROKEN: unused circular import between UserService and OrderService

from src.data import get_record


class UserService:
    def get_user(self, user_id: int) -> dict:
        return {"id": 1, "name": "Alice"}

    def get_name(self, user_id: int) -> str:
        user = self.get_user(user_id)
        return user.get("name", "Unknown")

    def get_order_count(self, user_id: int) -> int:
        orders = get_record("orders", user_id) or []
        return len(orders)
