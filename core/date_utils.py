"""
core/date_utils.py
--------------------
Pure month-range helpers, used by data_manager.py and several pages.
Zero dependency on streamlit so they can be unit tested directly — see
tests/test_date_utils.py. Previously each page had its own copy-pasted
version of this logic, which risked drifting out of sync (e.g. one page
using a fixed 3-month window while another used a proper range).
"""

from typing import List


def last_n_months(n: int, from_date: str) -> List[str]:
    """`from_date` is an ISO date string. Returns n YYYY-MM strings ending
    at from_date's month, most-recent first."""
    y, m = int(from_date[:4]), int(from_date[5:7])
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def months_between(date_from: str, date_to: str) -> List[str]:
    """`date_from`/`date_to` are ISO date strings. Returns YYYY-MM strings
    covering the range, oldest first."""
    y, m = int(date_from[:4]), int(date_from[5:7])
    ey, em = int(date_to[:4]), int(date_to[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def walk_months_backward(from_date: str, max_months: int) -> List[str]:
    """Like last_n_months but named for the history-search use case in
    data_manager.load_qc_history_for_level (kept as a thin, obviously-named
    alias so that call site reads clearly)."""
    return last_n_months(max_months, from_date)
