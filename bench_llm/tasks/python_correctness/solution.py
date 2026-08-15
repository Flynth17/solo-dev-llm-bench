"""Deliberately broken Python module for correctness benchmark.

Bugs:
  1. add(a, b) returns a - b instead of a + b
  2. multiply(x, y) has no return statement (returns None)
  3. is_even(n) checks odd numbers instead of even

Fix these bugs to pass all tests.
"""


def add(a, b):
    """Add two numbers and return the result."""
    return a - b  # BUG: should be a + b


def multiply(x, y):
    """Multiply two numbers and return the result."""
    result = x * y
    # BUG: missing return statement


def is_even(n):
    """Return True if n is even, False otherwise."""
    return n % 2 == 1  # BUG: should be n % 2 == 0