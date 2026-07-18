# SCENARIO: architectural_regression_3
# TYPE: Architectural (three-file circular import chain, load-bearing)
# BROKEN: get_payment_status() wrongly imports PaymentService directly
# instead of reading through the shared src.data layer, closing a
# three-file cycle: user -> payment -> order -> user.
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

    def get_payment_status(self, user_id: int) -> str:
        record = get_record("payments", user_id) or {}
        return record.get("status", "none")
