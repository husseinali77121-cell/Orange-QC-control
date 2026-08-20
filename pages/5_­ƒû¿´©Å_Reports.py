"""
pages/5_🖨️_Reports.py
------------------------
Generate a printable PDF QC summary for a test + control level over a
date range: control info, results log with Z-scores/status, the
Levey-Jennings chart, a violation summary, and sign-off lines.

Fixed after review:
  - PDF generation no longer crashes on the em-dash / subscript rule
    names — see core/pdf_report.py (now embeds a Unicode font).
  - This page reads the SAVED status/violated_rules from each record
    instead of re-running the Westgard engine, so the PDF always matches
    exactly what QC Entry showed at the time (see Levey-Jennings page for
    the full explanation of why recomputation was unsafe).
  - Branch is now a single required selection (not "All") for the PDF,
    since mixing two branches' independent QC into one printed summary
    would be misleading for a document meant to be signed off.
"""

import datetime as dt

import streamlit as st

from core import auth, data_manager
from core.models import TestDefinition
from core.westgard_engine import rule_display_name
from core.charts import build_lj_matplotlib, build_lj_matplotlib_zscore
from core.pdf_report import generate_qc_summary_pdf
from core.text_sanitize import has_non_latin_text

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

branch = st.selectbox("Branch", auth.get_branches(),
                       help="A signed report covers one branch's QC — pick the branch this "
                            "report is for.")
chart_mode = st.radio(
    "Chart mode", ["Standardized Z-score (recommended)", "Raw result units"],
    index=0, horizontal=True,
)
director_name = st.text_input("Reviewed & approved by (Laboratory Director)", value="Dr. Tarek El-Shafei")
lab_name = st.text_input("Lab name on report header", value="Orange Lab")
prepared_by = auth.current_operator()

if has_non_latin_text(director_name, lab_name, prepared_by):
    st.info(
        "ℹ️ One of the name fields contains Arabic text. The embedded PDF font doesn't include "
        "Arabic glyphs yet, so that field will show as **[non-Latin text]** in the PDF instead of "
        "crashing. Use Latin spelling for now if you need the name to appear correctly — "
        "Arabic branding is on the roadmap (see README)."
    )

if st.button("📄 Generate PDF", type="primary"):
    months = data_manager.months_between(date_from.isoformat(), date_to.isoformat())
    raw = data_manager.load_qc_records_range(months)
    raw = [r for r in raw if r["test_id"] == test_id and r["level_id"] == level_id and r["branch"] == branch]
    raw = [r for r in raw if date_from.isoformat() <= r["date"] <= date_to.isoformat()]
    raw.sort(key=lambda r: (r["date"], r["run_number"]))

    if not raw:
        st.warning("No QC results found for this test/level/branch/date range.")
        st.stop()

    lvl = td.levels[level_id]
    latest_version = lvl.active_version()
    spans_multiple_lots = len({(r["mean_used"], r["sd_used"]) for r in raw}) > 1
    if spans_multiple_lots and chart_mode == "Raw result units":
        st.warning("⚠️ This range spans more than one control lot — the raw-unit chart in the "
                   "PDF will reference the CURRENT lot's Mean/SD only. Consider switching to "
                   "Standardized Z-score for an always-correct chart.")

    # Build report rows straight from the SAVED verdict — no re-evaluation.
    points_for_chart = []
    for r in raw:
        z = (r["result"] - r["mean_used"]) / r["sd_used"] if r["sd_used"] else 0.0
        points_for_chart.append({
            "date": r["date"], "run_number": r["run_number"], "result": r["result"],
            "z": z, "status": r["overall_status"],
            "rule_names": [rule_display_name(rid) for rid in r.get("violated_rules", [])],
        })

    title = f"{td.test_name} — {lvl.control_name}"
    if chart_mode.startswith("Standardized"):
        chart_png = build_lj_matplotlib_zscore(points_for_chart, title=title)
    else:
        chart_png = build_lj_matplotlib(points_for_chart, latest_version.mean, latest_version.sd, title=title)

    # Not all runs in range necessarily used the same extended_rules
    # setting — reflect that honestly in the report instead of assuming.
    extended_flags = {r.get("extended_rules_enabled", False) for r in raw}
    extended_note = (
        extended_flags.pop() if len(extended_flags) == 1
        else True  # mixed settings: show as ON so the reader knows to check individual rows
    )

    pdf_bytes = generate_qc_summary_pdf(
        lab_name=lab_name, branch=branch,
        test_name=td.test_name, unit=td.unit, control_name=lvl.control_name,
        lot_number=latest_version.lot_number, mean=latest_version.mean, sd=latest_version.sd,
        date_range=f"{date_from.isoformat()} → {date_to.isoformat()}",
        records=points_for_chart, chart_png_bytes=chart_png,
        prepared_by=prepared_by, director_name=director_name,
        extended_rules_enabled=extended_note,
    )

    st.success(f"Report generated — {len(raw)} runs included.")
    st.download_button(
        "⬇️ Download PDF",
        data=pdf_bytes,
        file_name=f"OrangeLab_QC_{td.test_id}_{level_id}_{date_from}_{date_to}.pdf",
        mime="application/pdf",
    )
