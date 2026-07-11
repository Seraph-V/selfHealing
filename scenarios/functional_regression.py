# SCENARIO: functional_regression
# TYPE: Functional
# BROKEN: wrong arithmetic operators
# Replaces src/calculator.py to trigger CI failure.
from src.utils import safe_round


def add(a: float, b: float) -> float:
    return a - b


def subtract(a: float, b: float) -> float:
    return a + b


def multiply(a: float, b: float) -> float:
    return a + b


def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("Division by zero")
    return a / b


def average(numbers: list) -> float:
    return safe_round(sum(numbers) / len(numbers))
