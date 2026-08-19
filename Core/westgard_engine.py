"""
core/westgard_engine.py
------------------------
A real Westgard multirule QC engine — not a single-point z-score lookup.

It distinguishes:
  - WITHIN-RUN rules: compare different control LEVELS run together on the
    same run (2_2s within-run, R_4s). These need >=2 levels in the same run.
  - ACROSS-RUN rules: compare consecutive results of the SAME level over
    time (1_3s, 2_2s across-run, 4_1s, 10x, and optional extended rules).

Every rule carries a clinical-style verdict: status (warning/reject),
error type (random/systematic/trend), interpretation, and recommended
action — so the UI never just prints a rule code with no context.

This module has NO Streamlit / GitHub / IO dependency on purpose, so it can
be unit tested in isolation (see tests/test_westgard_engine.py).
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from itertools import combinations

from core.models import QCPoint


# ---------------------------------------------------------------------------
# Rule catalogue (data-driven, easy to extend / edit with Dr. Tarek's sign-off)
# ---------------------------------------------------------------------------

RULE_INFO: Dict[str, Dict[str, str]] = {
    "1_2s": {
        "name": "1\u2082s",
        "status": "warning",
        "error_type": "Screening / trigger rule",
        "interpretation": "One control result exceeded the mean \u00b12SD. "
                           "This rule alone does NOT reject the run — it is a trigger "
                           "to check the other rules before releasing results.",
        "action": "Inspect the other Westgard rules. If none of them are violated, "
                   "the run may be accepted; document the observation and watch the trend.",
    },
    "1_3s": {
        "name": "1\u2083s",
        "status": "reject",
        "error_type": "Random error",
        "interpretation": "One control result exceeded the mean \u00b13SD.",
        "action": "REJECT the run. Do not release patient results. Repeat the control. "
                   "Investigate pipetting, bubbles, mixing, sample/reagent handling, or a "
                   "one-off instrument glitch.",
    },
    "2_2s_within_run": {
        "name": "2\u2082s (within-run)",
        "status": "reject",
        "error_type": "Systematic error",
        "interpretation": "Two different control levels in the SAME run both exceeded "
                           "\u00b12SD on the SAME side of their means.",
        "action": "REJECT the run. Investigate calibration, reagent lot, or an instrument bias "
                   "affecting the whole measuring range.",
    },
    "2_2s_across_run": {
        "name": "2\u2082s (across-run)",
        "status": "reject",
        "error_type": "Systematic error",
        "interpretation": "This control level exceeded \u00b12SD on the SAME side in two "
                           "consecutive runs.",
        "action": "REJECT the run. Investigate calibration drift, reagent lot change, or "
                   "instrument bias for this analyte.",
    },
    "r_4s": {
        "name": "R\u2084s",
        "status": "reject",
        "error_type": "Random error",
        "interpretation": "The range between control levels WITHIN this run exceeded 4SD "
                           "(one level high, another low).",
        "action": "REJECT the run. Investigate pipetting precision, air bubbles, incomplete "
                   "mixing, or contamination of one of the controls.",
    },
    "4_1s": {
        "name": "4\u2081s",
        "status": "reject",
        "error_type": "Systematic error",
        "interpretation": "Four consecutive results for this level exceeded \u00b11SD on the "
                           "same side of the mean.",
        "action": "REJECT the run. Investigate calibration drift, reagent deterioration, or "
                   "a slow instrument bias — recalibrate if confirmed.",
    },
    "10x": {
        "name": "10x\u0304",
        "status": "reject",
        "error_type": "Systematic error",
        "interpretation": "Ten consecutive results for this level fell on the same side of "
                           "the mean (regardless of magnitude).",
        "action": "REJECT the run. Strong evidence of a systematic shift — check reagent "
                   "lot/expiry, calibration, and consider recalibration.",
    },
    "8x": {
        "name": "8x\u0304",
        "status": "reject",
        "error_type": "Systematic error (extended rule)",
        "interpretation": "Eight consecutive results for this level fell on the same side "
                           "of the mean.",
        "action": "REJECT the run. Investigate systematic shift (extended multirule — "
                   "enable only if your QC SOP specifies N=8/9/12 rules).",
    },
    "9x": {
        "name": "9x\u0304",
        "status": "reject",
        "error_type": "Systematic error (extended rule)",
        "interpretation": "Nine consecutive results for this level fell on the same side "
                           "of the mean.",
        "action": "REJECT the run. Investigate systematic shift.",
    },
    "12x": {
        "name": "12x\u0304",
        "status": "reject",
        "error_type": "Systematic error (extended rule)",
        "interpretation": "Twelve consecutive results for this level fell on the same side "
                           "of the mean.",
        "action": "REJECT the run. Investigate systematic shift.",
    },
    "7T": {
        "name": "7T",
        "status": "reject",
        "error_type": "Trend",
        "interpretation": "Seven consecutive results for this level are steadily trending "
                           "in the same direction (up or down).",
        "action": "REJECT the run. Investigate progressive reagent deterioration, calibrator "
                   "instability, or light source/lamp aging.",
    },
}


@dataclass
class RuleViolation:
    rule_id: str
    rule_name: str
    status: str                      # "warning" | "reject"
    scope: str                       # "within_run" | "across_run"
    levels_involved: List[str]
    error_type: str
    interpretation: str
    action: str


@dataclass
class RunEvaluation:
    overall_status: str              # "in_control" | "warning" | "reject"
    violations: List[RuleViolation] = field(default_factory=list)
    per_level_z: Dict[str, float] = field(default_factory=dict)

    def summary_line(self) -> str:
        if self.overall_status == "reject":
            names = ", ".join(v.rule_name for v in self.violations if v.status == "reject")
            return f"REJECT — {names}"
        if self.overall_status == "warning":
            names = ", ".join(v.rule_name for v in self.violations)
            return f"WARNING — {names}"
        return "IN CONTROL"


def _same_side(z_values: List[float]) -> bool:
    """True if all z-scores are on the same side of the mean (all >0 or all <0)."""
    if not z_values:
        return False
    if any(z == 0 for z in z_values):
        return False
    signs = {z > 0 for z in z_values}
    return len(signs) == 1


def _consecutive_same_side(series_z: List[float], n: int) -> bool:
    if len(series_z) < n:
        return False
    return _same_side(series_z[-n:])


def _is_trend(series_z: List[float], n: int = 7) -> Optional[str]:
    """Return 'up' / 'down' if the last n points are strictly monotonic, else None."""
    if len(series_z) < n:
        return None
    window = series_z[-n:]
    if all(window[i] < window[i + 1] for i in range(len(window) - 1)):
        return "up"
    if all(window[i] > window[i + 1] for i in range(len(window) - 1)):
        return "down"
    return None


def _make_violation(rule_id: str, scope: str, levels: List[str]) -> RuleViolation:
    info = RULE_INFO[rule_id]
    return RuleViolation(
        rule_id=rule_id,
        rule_name=info["name"],
        status=info["status"],
        scope=scope,
        levels_involved=levels,
        error_type=info["error_type"],
        interpretation=info["interpretation"],
        action=info["action"],
    )


def evaluate_run(
    new_points: List[QCPoint],
    history: List[QCPoint],
    extended_rules: bool = False,
) -> RunEvaluation:
    """
    Evaluate one QC run (one or more control levels entered together)
    against Westgard multirules.

    new_points : the QCPoints just entered for this run (same test, same
                 date/run_number, 1-3 different levels).
    history    : ALL previous QCPoints for this test (any level), NOT
                 including new_points, sorted or unsorted (we sort here).
    extended_rules : if True, also check 8x / 9x / 12x / 7T.
    """
    violations: List[RuleViolation] = []
    per_level_z: Dict[str, float] = {p.level_id: round(p.z, 3) for p in new_points}
    reject_levels_from_across_run = set()

    # ---- WITHIN-RUN rules (need >=2 different levels in this same run) ----
    if len(new_points) >= 2:
        for a, b in combinations(new_points, 2):
            if a.level_id == b.level_id:
                continue
            za, zb = a.z, b.z

            # 2_2s within-run: both beyond 2SD, same side
            if abs(za) >= 2 and abs(zb) >= 2 and _same_side([za, zb]):
                violations.append(_make_violation(
                    "2_2s_within_run", "within_run", [a.level_id, b.level_id]
                ))
                reject_levels_from_across_run.update([a.level_id, b.level_id])

            # R_4s: range between the two levels' z-scores >= 4SD
            if abs(za - zb) >= 4:
                violations.append(_make_violation(
                    "r_4s", "within_run", [a.level_id, b.level_id]
                ))
                reject_levels_from_across_run.update([a.level_id, b.level_id])

    # ---- ACROSS-RUN rules (per level, using chronological history) ----
    history_by_level: Dict[str, List[QCPoint]] = {}
    for p in history:
        history_by_level.setdefault(p.level_id, []).append(p)

    for np in new_points:
        series = sorted(
            history_by_level.get(np.level_id, []) + [np], key=lambda p: p.sort_key
        )
        series_z = [p.z for p in series]
        level_has_reject = False

        # 1_3s
        if abs(np.z) >= 3:
            violations.append(_make_violation("1_3s", "across_run", [np.level_id]))
            level_has_reject = True

        # 2_2s across-run (last two points at this level)
        if len(series_z) >= 2:
            last_two = series_z[-2:]
            if all(abs(z) >= 2 for z in last_two) and _same_side(last_two):
                violations.append(_make_violation("2_2s_across_run", "across_run", [np.level_id]))
                level_has_reject = True

        # 4_1s (last four points at this level)
        if _consecutive_same_side(series_z, 4) and all(abs(z) >= 1 for z in series_z[-4:]):
            violations.append(_make_violation("4_1s", "across_run", [np.level_id]))
            level_has_reject = True

        # 10x
        if _consecutive_same_side(series_z, 10):
            violations.append(_make_violation("10x", "across_run", [np.level_id]))
            level_has_reject = True

        if extended_rules:
            if _consecutive_same_side(series_z, 12):
                violations.append(_make_violation("12x", "across_run", [np.level_id]))
                level_has_reject = True
            elif _consecutive_same_side(series_z, 9):
                violations.append(_make_violation("9x", "across_run", [np.level_id]))
                level_has_reject = True
            elif _consecutive_same_side(series_z, 8):
                violations.append(_make_violation("8x", "across_run", [np.level_id]))
                level_has_reject = True

            if _is_trend(series_z, 7) is not None:
                violations.append(_make_violation("7T", "across_run", [np.level_id]))
                level_has_reject = True

        if level_has_reject:
            reject_levels_from_across_run.add(np.level_id)

        # 1_2s — only report as its own (warning) entry if nothing else
        # already rejected this level, to avoid noisy duplicate output.
        if np.level_id not in reject_levels_from_across_run and abs(np.z) >= 2:
            violations.append(_make_violation("1_2s", "across_run", [np.level_id]))

    # ---- overall status ----
    if any(v.status == "reject" for v in violations):
        overall = "reject"
    elif any(v.status == "warning" for v in violations):
        overall = "warning"
    else:
        overall = "in_control"

    return RunEvaluation(overall_status=overall, violations=violations, per_level_z=per_level_z)
