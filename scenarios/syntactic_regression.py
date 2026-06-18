# SCENARIO: syntactic_regression
# TYPE: Syntactic (PEP8 / flake8)
# BROKEN: E711 — comparison to None (use 'is None' instead)
# This file replaces src/utils.py to trigger the lint job failure.
import os


def get_env(key, default=None):
    value = os.environ.get(key, default)
    if value == None:
        return default
    return value


def clamp(value, lo, hi):
    return max(lo, min(value, hi))
