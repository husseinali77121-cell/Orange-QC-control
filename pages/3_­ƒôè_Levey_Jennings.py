"""
pages/3_📊_Levey_Jennings.py
------------------------------
Interactive Levey-Jennings chart per test + control level, with rule
violations annotated directly on the plot so shifts/trends/random errors
are visible at a glance.

Fixed after review (this was the most important logic bug found):
  This page used to re-run the Westgard engine ONE LEVEL AT A TIME to
  build the chart, which could produce a DIFFERENT verdict than what QC
  Entry showed and saved — e.g. Entry correctly reports "REJECT — 2-2s
  within-run" for a two-level run, but re-evaluating Level 1 alone here
  only saw "WARNING — 1-2s". A QC program must never show two different
  verdicts for the same run.

  The fix: this page no longer calls evaluate_run() at all. It reads
  `overall_status` and `violated_rules` straight from the saved record —
  whatever was decided (and persisted) at entry time is the single source
  of truth, everywhere it's displayed.

  Also added: a "standardized Z-score" chart mode (fixed bands at
  ±1/2/3 SD regardless of lot), recommended — and used by default —
  whenever the selected range spans more than one control lot, instead of
  plotting raw results from two different lots against one (wrong) set of
  reference bands.
"""

import datetime as dt

import streamlit as st

from core import auth, data_manager
from core.models import TestDefinition
from core.westgard_engine import rule_display_name
from core.charts import build_lj_plotly, build_lj_plotly_zscore

st.set_page_config(page_title="Levey-Jennings — Orange Lab QC", page_icon="📊", layout="wide")

if not auth.require_login():
    st.stop()
auth.logout_button()

st.title("📊 Levey-Jennings Chart")

defs = data_manager.load_test_definitions().get("tests", {})
if not defs:
    st.info("No tests configured yet.")
    st.stop()

c1, c2, c3 = st.columns(3)
test_id = c1.selectbox("Test", list(defs.keys()), format_func=lambda k: defs[k]["test_name"])
td = TestDefinition.from_dict(defs[test_id])
level_id = c2.selectbox("Control level", list(td.levels.keys()),
                         format_func=lambda k: td.levels[k].control_name)
n_months_back = c3.slider("Months of history", 1, 12, 3)

branch_filter = st.selectbox("Branch", auth.get_branches())

months = data_manager.last_n_months(n_months_back, dt.date.today().isoformat())
raw = data_manager.load_qc_records_range(months)
raw = [r for r in raw if r["test_id"] == test_id and r["level_id"] == level_id and r["branch"] == branch_filter]
raw.sort(key=lambda r: (r["date"], r["run_number"]))

if not raw:
    st.info("No QC results yet for this test/level/branch in the selected window.")
    st.stop()

lvl = td.levels[level_id]
latest_version = lvl.active_version()
spans_multiple_lots = len({(r["mean_used"], r["sd_used"]) for r in raw}) > 1

chart_mode = st.radio(
    "Chart mode",
    ["Standardized Z-score (recommended)", "Raw result units"],
    index=0,
    horizontal=True,
    help="Standardized Z-score always uses fixed ±1/2/3 SD bands and is safe to use across a "
         "lot change. Raw result units are easier to read at a glance but only make sense "
         "within a single lot's mean/SD.",
)
if spans_multiple_lots and chart_mode == "Raw result units":
    st.warning(
        "⚠️ This range spans more than one control lot (different Mean/SD were active at "
        "different times). Raw-unit bands below use the CURRENT lot's Mean/SD for reference — "
        "older points from a previous lot may look artificially off-target. Switch to "
        "**Standardized Z-score** for an always-correct view across the lot change."
    )

# Build chart points straight from the SAVED verdict — no re-evaluation.
points = []
for r in raw:
    z = (r["result"] - r["mean_used"]) / r["sd_used"] if r["sd_used"] else 0.0
    points.append({
        "date": r["date"], "run_number": r["run_number"], "result": r["result"],
        "z": z, "status": r["overall_status"],
        "rule_names": [rule_display_name(rid) for rid in r.get("violated_rules", [])],
    })

if chart_mode.startswith("Standardized"):
    fig = build_lj_plotly_zscore(points, title=f"{td.test_name} — {lvl.control_name}")
else:
    fig = build_lj_plotly(points, latest_version.mean, latest_version.sd,
                           title=f"{td.test_name} — {lvl.control_name}")
st.plotly_chart(fig, use_container_width=True)

n_reject = sum(1 for p in points if p["status"] == "reject")
n_warn = sum(1 for p in points if p["status"] == "warning")
c1, c2, c3 = st.columns(3)
c1.metric("Total points", len(points))
c2.metric("Rejected", n_reject)
c3.metric("Warnings", n_warn)
