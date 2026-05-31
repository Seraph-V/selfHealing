"""
SCENARIO: architectural_regression
TYPE: Architectural (circular import)
BROKEN: UserService imports OrderService which imports UserService.

This file replaces src/services/user.py to trigger the circular import.
The agent must remove the cross-service import and use src.data instead.
"""
from src.services.order import OrderService  # BUG: creates circular dependency


class UserService:
    def get_user(self, user_id: int) -> dict:
        return {"id": user_id, "name": "Alice"}

    def get_name(self, user_id: int) -> str:
        user = self.get_user(user_id)
        return user.get("name", "Unknown")

    def get_user_orders(self, user_id: int) -> list:
        return OrderService().get_orders_for(user_id)
