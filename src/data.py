# SCENARIO: syntactic_regression_2
# TYPE: Syntactic (PEP8 / flake8)
# BROKEN: E711 comparison to None in data.py
# This file replaces src/data.py to trigger the lint job failure.
_STORE: dict = {
    "users":  {1: {"name": "Alice"}, 2: {"name": "Bob"}},
    "orders": {1: ["order_101", "order_102"], 2: []},
}


def get_record(table: str, key: int):
    result = _STORE.get(table, {}).get(key)
    if result is None:
        return None
    return result
