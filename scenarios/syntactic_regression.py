"""
SCENARIO: syntactic_regression
TYPE: Syntactic (PEP8 / flake8)
BROKEN: Multiple style violations.

This file replaces src/utils.py to trigger the lint job failure.
Violations: E302, E711, W291, E501
"""
import os


def get_env(key: str, default: str = None) -> str:
    value = os.environ.get(key, default)
    if value == None:  # noqa violation: E711 — should use 'is None'   
        return default
    return value  # trailing whitespace above (W291)
def clamp(value: float, min_val: float, max_val: float) -> float:  # E302: missing blank lines
    """This function name is fine but the line below is too long (E501) xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"""
    return max(min_val, min(value, max_val))
