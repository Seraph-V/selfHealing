# SCENARIO: functional_regression_2
# TYPE: Functional
# BROKEN: clamp() returns inverted min/max (lo/hi swapped)
# Replaces src/utils.py to trigger CI failure.
import os

import yaml


def get_env(key, default=None):
    value = os.environ.get(key, default)
    if value is None:
        return default
    return value


def clamp(value, lo, hi):
    return max(lo, min(value, hi))


def safe_round(value, digits=2):
    return round(value, digits)


def load_config(text):
    return yaml.safe_load(text)
