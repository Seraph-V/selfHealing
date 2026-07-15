# SCENARIO: syntactic_regression_3
# TYPE: Syntactic (PEP8 / flake8) - multiple simultaneous violations
# BROKEN: E711 + F841 (unused variable) + E501 (line too long), all at once.
# Replaces src/data.py to trigger the lint job failure.
_STORE: dict = {
    "users": {1: {"name": "Alice"}, 2: {"name": "Bob"}},
    "orders": {1: ["order_101", "order_102"], 2: []},
}


def get_record(table: str, key: int):
    result = _STORE.get(table, {}).get(key)
    if result is None:
        return None
    return result
