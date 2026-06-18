# SCENARIO: architectural_regression
# TYPE: Architectural (circular import)
# BROKEN: circular import between UserService and OrderService
from src.services.order import OrderService


class UserService:
    def get_user(self, user_id: int) -> dict:
        return {"id": 1, "name": "Alice"}

    def get_name(self, user_id: int) -> str:
        user = self.get_user(user_id)
        return user.get("name", "Unknown")
