# SCENARIO: syntactic_regression
# TYPE: Syntactic (PEP8 / flake8)
# BROKEN: E711 â€” comparison to None (use 'is None' instead)
# This file replaces src/utils.py to trigger the lint job failure.
import os

import yaml


def get_env(key, default=None):
    value = os.environ.get(key, default)
    if value == None:
        return default
    return value


def clamp(value, lo, hi):
    return max(lo, min(value, hi))


def safe_round(value, digits=2):
    return round(value, digits)


def load_config(text):
    return yaml.safe_load(text)
