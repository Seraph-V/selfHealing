# SCENARIO: functional_regression_2
# TYPE: Functional
# BROKEN: clamp() returns inverted min/max (lo/hi swapped)
# Replaces src/utils.py to trigger CI failure.
import os


def get_env(key, default=None):
    value = os.environ.get(key, default)
    if value is None:
        return default
    return value


def clamp(value, lo, hi):
    return min(lo, max(value, hi))
