# SCENARIO: architectural_regression
# TYPE: Architectural (circular import)
# BROKEN: circular import between OrderService and UserService
from src.services.user import UserService


class OrderService:
    def get_orders_for(self, user_id: int) -> list:
        return ["order_101", "order_102"]
