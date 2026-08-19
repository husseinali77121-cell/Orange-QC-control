"""
pages/5_🖨️_Reports.py
------------------------
Generate a printable PDF QC summary for a test + control level over a
date range: control info, results log with Z-scores/status, the
Levey-Jennings chart, a violation summary, and sign-off lines.
"""

import datetime as dt

import streamlit as st

from core import auth, data_manager
from core.models import TestDefinition, QCPoint
from core.westgard_engine import evaluate_run
from core.charts import build_lj_matplotlib
from core.pdf_report import generate_qc_summary_pdf

st.set_page_config(page_title="Reports — Orange Lab QC", page_icon="🖨️", layout="wide")

if not auth.require_login():
    st.stop()
auth.logout_button()

st.title("🖨️ QC Summary Report (PDF)")

defs = data_manager.load_test_definitions().get("tests", {})
if not defs:
    st.info("No tests configured yet.")
    st.stop()

c1, c2 = st.columns(2)
test_id = c1.selectbox("Test", list(defs.keys()), format_func=lambda k: defs[k]["test_name"])
td = TestDefinition.from_dict(defs[test_id])
level_id = c2.selectbox("Control level", list(td.levels.keys()),
                         format_func=lambda k: td.levels[k].control_name)

c3, c4 = st.columns(2)
date_from = c3.date_input("From", value=dt.date.today().replace(day=1))
date_to = c4.date_input("To", value=dt.date.today())

branch_filter = st.selectbox("Branch", ["All"] + auth.get_branches())
extended_rules = st.checkbox("Extended rules were/are used (8x/9x/12x/7T)", value=False)
director_name = st.text_input("Reviewed & approved by (Laboratory Director)", value="Dr. Tarek El-Shafei")
lab_name = st.text_input("Lab name on report header", value="Orange Lab")


def months_between(d1, d2):
    months = []
    y, m = d1.year, d1.month
    while (y, m) <= (d2.year, d2.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m = 1
            y += 1
    return months


if st.button("📄 Generate PDF", type="primary"):
    months = months_between(date_from, date_to)
    raw = data_manager.load_qc_records_range(months)
    raw = [r for r in raw if r["test_id"] == test_id and r["level_id"] == level_id]
    raw = [r for r in raw if date_from.isoformat() <= r["date"] <= date_to.isoformat()]
    if branch_filter != "All":
        raw = [r for r in raw if r["branch"] == branch_filter]
    raw.sort(key=lambda r: (r["date"], r["run_number"]))

    if not raw:
        st.warning("No QC results found for this test/level/date range.")
        st.stop()

    lvl = td.levels[level_id]
    latest_version = lvl.active_version()

    points_for_chart = []
    history = []
    for r in raw:
        p = QCPoint(level_id=r["level_id"], date=r["date"], run_number=r["run_number"],
                    result=r["result"], mean=r["mean_used"], sd=r["sd_used"], record_id=r["id"])
        ev = evaluate_run([p], history, extended_rules=extended_rules)
        points_for_chart.append({
            "date": r["date"], "run_number": r["run_number"], "result": r["result"],
            "z": p.z, "status": ev.overall_status,
            "rule_names": [v.rule_name for v in ev.violations],
        })
        history.append(p)

    chart_png = build_lj_matplotlib(
        points_for_chart, latest_version.mean, latest_version.sd,
        title=f"{td.test_name} — {lvl.control_name}"
    )

    pdf_bytes = generate_qc_summary_pdf(
        lab_name=lab_name,
        branch=(branch_filter if branch_filter != "All" else "All branches"),
        test_name=td.test_name, unit=td.unit, control_name=lvl.control_name,
        lot_number=latest_version.lot_number, mean=latest_version.mean, sd=latest_version.sd,
        date_range=f"{date_from.isoformat()} → {date_to.isoformat()}",
        records=points_for_chart, chart_png_bytes=chart_png,
        prepared_by=auth.current_operator(), director_name=director_name,
        extended_rules_enabled=extended_rules,
    )

    st.success(f"Report generated — {len(raw)} runs included.")
    st.download_button(
        "⬇️ Download PDF",
        data=pdf_bytes,
        file_name=f"OrangeLab_QC_{td.test_id}_{level_id}_{date_from}_{date_to}.pdf",
        mime="application/pdf",
    )
