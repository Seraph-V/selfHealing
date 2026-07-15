# SCENARIO: functional_regression_3
# TYPE: Functional (indirect â€” bug is in a shared helper, not the tested file)
# BROKEN: safe_round() rounds to one digit fewer than requested (off-by-one)
# Replaces src/utils.py to trigger CI failure. The failing test imports
# src.calculator.average(), which internally depends on src.utils.safe_round().
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
    return round(value, digits)  # Fixed: removed the off-by-one error


def load_config(text):
    return yaml.safe_load(text)
