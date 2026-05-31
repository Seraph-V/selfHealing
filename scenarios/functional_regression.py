"""
SCENARIO: functional_regression
TYPE: Functional
BROKEN: Wrong operators in arithmetic functions.

This file replaces src/calculator.py to trigger the CI failure.
The agent must fix the operators: - → +, + → -, + → *
"""


def add(a: float, b: float) -> float:
    return a - b  # BUG: should be +


def subtract(a: float, b: float) -> float:
    return a + b  # BUG: should be -


def multiply(a: float, b: float) -> float:
    return a + b  # BUG: should be *


def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("Division by zero")
    return a / b
