from src.data import get_record


class PaymentService:
    def get_payment_status(self, user_id: int) -> str:
        record = get_record("payments", user_id) or {}
        return record.get("status", "none")

    def get_order_count_for_payment(self, user_id: int) -> int:
        orders = get_record("orders", user_id) or []
        return len(orders)
