"""
pages/3_📊_Levey_Jennings.py
------------------------------
Interactive Levey-Jennings chart per test + control level, with rule
violations annotated directly on the plot so shifts/trends/random errors
are visible at a glance.
"""

import datetime as dt

import streamlit as st

from core import auth, data_manager
from core.models import TestDefinition, QCPoint
from core.westgard_engine import evaluate_run
from core.charts import build_lj_plotly

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

branch_filter = st.selectbox("Branch", ["All"] + auth.get_branches())
extended_rules = st.checkbox("Extended rules were/are used (8x/9x/12x/7T)", value=False)


def last_n_months(n, from_date):
    out = []
    y, m = from_date.year, from_date.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


months = last_n_months(n_months_back, dt.date.today())
raw = data_manager.load_qc_records_range(months)
raw = [r for r in raw if r["test_id"] == test_id and r["level_id"] == level_id]
if branch_filter != "All":
    raw = [r for r in raw if r["branch"] == branch_filter]
raw.sort(key=lambda r: (r["date"], r["run_number"]))

if not raw:
    st.info("No QC results yet for this test/level in the selected window.")
    st.stop()

lvl = td.levels[level_id]
latest_version = lvl.active_version()
mean, sd = latest_version.mean, latest_version.sd
if len({(r["mean_used"], r["sd_used"]) for r in raw}) > 1:
    st.warning(
        "⚠️ This range spans more than one control lot (different mean/SD were used at different "
        "times). The chart below plots raw results against the CURRENT mean/SD bands for visual "
        "reference — individual Z-scores in the table still use the lot that was active on each date."
    )

# Re-run the engine chronologically so the chart shows exactly the same
# verdict the operator saw at entry time (uses each point's own recorded mean/sd).
points = []
history = []
for r in raw:
    p = QCPoint(level_id=r["level_id"], date=r["date"], run_number=r["run_number"],
                result=r["result"], mean=r["mean_used"], sd=r["sd_used"], record_id=r["id"])
    ev = evaluate_run([p], history, extended_rules=extended_rules)
    points.append({
        "date": r["date"], "run_number": r["run_number"], "result": r["result"],
        "z": p.z, "status": ev.overall_status,
        "rule_names": [v.rule_name for v in ev.violations],
    })
    history.append(p)

fig = build_lj_plotly(points, mean, sd, title=f"{td.test_name} — {lvl.control_name}")
st.plotly_chart(fig, use_container_width=True)

n_reject = sum(1 for p in points if p["status"] == "reject")
n_warn = sum(1 for p in points if p["status"] == "warning")
c1, c2, c3 = st.columns(3)
c1.metric("Total points", len(points))
c2.metric("Rejected", n_reject)
c3.metric("Warnings", n_warn)
