"""
UserService — no circular import (correct implementation).
Depends only on a shared data layer, not on OrderService.
"""
from src.data import get_record


class UserService:
    def get_user(self, user_id: int) -> dict:
        return get_record("users", user_id)

    def get_name(self, user_id: int) -> str:
        user = self.get_user(user_id)
        return user.get("name", "Unknown")
