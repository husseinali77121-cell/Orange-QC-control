"""
tests/test_westgard_engine.py
------------------------------
Pure-logic unit tests for the Westgard multirule engine.
Run with:  python -m pytest tests/ -v
(No Streamlit / network needed — this only imports core.models / core.westgard_engine)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import QCPoint
from core.westgard_engine import evaluate_run, RULE_INFO, rule_display_name


def pt(level, d, run, result, mean, sd):
    return QCPoint(level_id=level, date=d, run_number=run, result=result, mean=mean, sd=sd)


def test_in_control_normal_result():
    new = [pt("level_1", "2026-08-19", 1, 101, 100, 3)]  # z = 0.33
    ev = evaluate_run(new, history=[])
    assert ev.overall_status == "in_control"
    assert ev.violations == []


def test_1_2s_warning_only():
    new = [pt("level_1", "2026-08-19", 1, 107, 100, 3)]  # z = 2.33
    ev = evaluate_run(new, history=[])
    assert ev.overall_status == "warning"
    assert ev.violations[0].rule_id == "1_2s"


def test_1_3s_reject():
    new = [pt("level_1", "2026-08-19", 1, 110, 100, 3)]  # z = 3.33
    ev = evaluate_run(new, history=[])
    assert ev.overall_status == "reject"
    assert any(v.rule_id == "1_3s" for v in ev.violations)


def test_2_2s_within_run_reject():
    new = [
        pt("level_1", "2026-08-19", 1, 107, 100, 3),   # z=+2.33
        pt("level_2", "2026-08-19", 1, 227, 200, 10),  # z=+2.7
    ]
    ev = evaluate_run(new, history=[])
    assert ev.overall_status == "reject"
    assert any(v.rule_id == "2_2s_within_run" for v in ev.violations)


def test_2_2s_within_run_not_triggered_when_opposite_sides():
    new = [
        pt("level_1", "2026-08-19", 1, 107, 100, 3),   # z=+2.33
        pt("level_2", "2026-08-19", 1, 178, 200, 10),  # z=-2.2
    ]
    ev = evaluate_run(new, history=[])
    # This should hit R_4s instead (range >= 4SD), not 2_2s (opposite sides)
    assert any(v.rule_id == "r_4s" for v in ev.violations)
    assert not any(v.rule_id == "2_2s_within_run" for v in ev.violations)


def test_r_4s_reject():
    new = [
        pt("level_1", "2026-08-19", 1, 106, 100, 3),   # z=+2.0
        pt("level_2", "2026-08-19", 1, 178, 200, 10),  # z=-2.2
    ]
    ev = evaluate_run(new, history=[])
    assert ev.overall_status == "reject"
    assert any(v.rule_id == "r_4s" for v in ev.violations)


def test_2_2s_across_run_reject():
    history = [pt("level_1", "2026-08-18", 1, 107, 100, 3)]   # z=+2.33
    new = [pt("level_1", "2026-08-19", 1, 106.5, 100, 3)]      # z=+2.17
    ev = evaluate_run(new, history)
    assert ev.overall_status == "reject"
    assert any(v.rule_id == "2_2s_across_run" for v in ev.violations)


def test_4_1s_reject():
    history = [
        pt("level_1", "2026-08-16", 1, 104, 100, 3),  # z=1.33
        pt("level_1", "2026-08-17", 1, 104, 100, 3),
        pt("level_1", "2026-08-18", 1, 104, 100, 3),
    ]
    new = [pt("level_1", "2026-08-19", 1, 104, 100, 3)]
    ev = evaluate_run(new, history)
    assert ev.overall_status == "reject"
    assert any(v.rule_id == "4_1s" for v in ev.violations)


def test_10x_reject():
    history = [pt("level_1", f"2026-08-{10+i:02d}", 1, 101, 100, 3) for i in range(9)]
    new = [pt("level_1", "2026-08-19", 1, 100.5, 100, 3)]
    ev = evaluate_run(new, history)
    assert ev.overall_status == "reject"
    assert any(v.rule_id == "10x" for v in ev.violations)


def test_9x_and_8x_ignored_without_extended_flag():
    history = [pt("level_1", f"2026-08-{10+i:02d}", 1, 101, 100, 3) for i in range(8)]
    new = [pt("level_1", "2026-08-19", 1, 100.5, 100, 3)]  # total 9 same-side, but only 9
    ev = evaluate_run(new, history, extended_rules=False)
    assert not any(v.rule_id in ("8x", "9x") for v in ev.violations)
    ev2 = evaluate_run(new, history, extended_rules=True)
    assert any(v.rule_id == "9x" for v in ev2.violations)


def test_7T_trend_reject_when_extended():
    history = [pt("level_1", f"2026-08-{10+i:02d}", 1, 100 + i * 0.5, 100, 3) for i in range(6)]
    new = [pt("level_1", "2026-08-19", 1, 103.5, 100, 3)]
    ev = evaluate_run(new, history, extended_rules=True)
    assert any(v.rule_id == "7T" for v in ev.violations)


def test_independent_levels_dont_cross_contaminate():
    # A run at level_1 only should not evaluate within-run rules
    new = [pt("level_1", "2026-08-19", 1, 101, 100, 3)]
    ev = evaluate_run(new, history=[])
    assert not any(v.scope == "within_run" for v in ev.violations)


def test_all_rule_names_are_pdf_and_csv_safe():
    # Regression guard: fpdf2's core fonts (and some CSV viewers) can't
    # render subscript/combining Unicode. Every rule must expose a plain
    # ASCII fallback so PDF generation can never crash on a rule label.
    for rule_id, info in RULE_INFO.items():
        assert "name_ascii" in info, f"{rule_id} missing name_ascii"
        info["name_ascii"].encode("ascii")  # raises if not pure ASCII


def test_rule_display_name_lookup_matches_engine_output():
    new = [pt("level_1", "2026-08-19", 1, 110, 100, 3)]  # 1_3s
    ev = evaluate_run(new, history=[])
    violated_id = ev.violations[0].rule_id
    assert rule_display_name(violated_id) == ev.violations[0].rule_name
    assert rule_display_name(violated_id, ascii_safe=True) == ev.violations[0].rule_name_ascii


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
