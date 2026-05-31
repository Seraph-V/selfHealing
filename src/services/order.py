"""
OrderService — no circular import (correct implementation).
"""
from src.data import get_record


class OrderService:
    def get_orders_for(self, user_id: int) -> list:
        return get_record("orders", user_id) or []
