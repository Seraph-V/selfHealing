"""
Utility helpers — correct (green) implementation.
"""
import os


def get_env(key: str, default: str = None) -> str:
    value = os.environ.get(key, default)
    if value is None:
        return default
    return value


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value between min and max."""
    return max(min_val, min(value, max_val))
