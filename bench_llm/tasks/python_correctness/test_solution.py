"""Pytest tests for solution.py correctness benchmark.

These tests validate the three core functions of the solution module.
DO NOT MODIFY this file during the benchmark — the model must fix solution.py.
"""

import sys
import os

# Allow importing from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solution import add, multiply, is_even


def test_add_positive_numbers():
    """Test adding two positive integers."""
    assert add(2, 3) == 5


def test_add_negative_numbers():
    """Test adding two negative integers."""
    assert add(-1, -1) == -2


def test_multiply_positive_numbers():
    """Test multiplying two positive integers."""
    assert multiply(4, 5) == 20


def test_multiply_by_zero():
    """Test multiplying by zero."""
    assert multiply(10, 0) == 0


def test_is_even_true():
    """Test is_even returns True for even numbers."""
    assert is_even(4) is True


def test_is_even_false():
    """Test is_even returns False for odd numbers."""
    assert is_even(3) is False