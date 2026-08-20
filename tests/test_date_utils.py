"""
tests/test_date_utils.py
--------------------------
Unit tests for the month-range helpers every page/data_manager function
relies on (history window sizing, report date ranges).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.date_utils import last_n_months, months_between


def test_last_n_months_basic():
    assert last_n_months(3, "2026-08-19") == ["2026-08", "2026-07", "2026-06"]


def test_last_n_months_crosses_year_boundary():
    assert last_n_months(4, "2026-02-05") == ["2026-02", "2026-01", "2025-12", "2025-11"]


def test_last_n_months_single_month():
    assert last_n_months(1, "2026-08-19") == ["2026-08"]


def test_months_between_same_month():
    assert months_between("2026-08-01", "2026-08-31") == ["2026-08"]


def test_months_between_multi_month():
    assert months_between("2026-06-15", "2026-09-02") == ["2026-06", "2026-07", "2026-08", "2026-09"]


def test_months_between_crosses_year_boundary():
    assert months_between("2025-11-01", "2026-02-01") == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_last_n_months_enough_for_12x_rule():
    # Regression guard for the "3 months is too short" bug: make sure we
    # can request enough months to safely cover a 12x rule window even for
    # a test run only once a month.
    months = last_n_months(13, "2026-08-19")
    assert len(months) == 13
    assert len(set(months)) == 13  # no duplicates


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
