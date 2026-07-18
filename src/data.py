"""
Minimal in-memory data layer shared across services.
Avoids cross-service imports that cause circular dependencies.
"""

_STORE: dict = {
    "users":    {1: {"name": "Alice"}, 2: {"name": "Bob"}},
    "orders":   {1: ["order_101", "order_102"], 2: []},
    "payments": {1: {"status": "paid"}, 2: {"status": "pending"}},
}


def get_record(table: str, key: int):
    return _STORE.get(table, {}).get(key)
