"""
SCENARIO: architectural_regression  
TYPE: Architectural (circular import)
BROKEN: OrderService imports UserService which imports OrderService.

This file replaces src/services/order.py — pair with architectural_regression_user.py
"""
from src.services.user import UserService  # BUG: creates circular dependency


class OrderService:
    def get_orders_for(self, user_id: int) -> list:
        return ["order_101", "order_102"]

    def get_user_name(self, user_id: int) -> str:
        return UserService().get_name(user_id)
