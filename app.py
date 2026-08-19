"""
app.py
-------
Orange Lab — BK-280 QC / Westgard Decision Support Program

Run locally:
    streamlit run app.py

Pages (left sidebar, auto-discovered by Streamlit from /pages):
  1) Test Setup        -> define tests, control levels, mean/SD, lot numbers
  2) QC Entry           -> enter today's control results, get an instant
                            Westgard verdict (status / rule / interpretation / action)
  3) Levey-Jennings      -> interactive chart per test/level
  4) QC History          -> filterable audit log of every QC run
  5) Reports             -> generate a printable PDF summary
"""

import datetime as dt

import streamlit as st

from core import auth, data_manager
from core.models import TestDefinition

st.set_page_config(page_title="Orange Lab — BK-280 QC", page_icon="🧪", layout="wide")

if not auth.require_login():
    st.stop()

auth.logout_button()

st.title("🧪 Orange Lab — BK-280 QC / Westgard")
st.caption(f"Signed in as **{auth.current_operator()}** · Branch: **{auth.current_branch()}** · "
           f"Storage: `{data_manager.storage_mode()}`")

defs = data_manager.load_test_definitions().get("tests", {})

if not defs:
    st.info(
        "لسه معملتش أي تحليل. ابدأ من صفحة **⚙️ Test Setup** في القائمة الجانبية عشان "
        "تضيف التحاليل ومستويات الكنترول (Mean / SD / Lot) بتاعتها.\n\n"
        "No tests configured yet — start on the **Test Setup** page to add your analytes "
        "and control levels (Mean / SD / Lot number)."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Quick dashboard: current month's QC status across all tests
# ---------------------------------------------------------------------------

today = dt.date.today()
yyyymm = today.strftime("%Y-%m")
records = data_manager.load_qc_records(yyyymm)
records = [r for r in records if r.get("branch") == auth.current_branch()]

st.subheader(f"📋 This month at a glance — {auth.current_branch()} ({yyyymm})")

col1, col2, col3, col4 = st.columns(4)
n_total = len(records)
n_reject = sum(1 for r in records if r.get("overall_status") == "reject")
n_warn = sum(1 for r in records if r.get("overall_status") == "warning")
n_open_capa = sum(1 for r in records if r.get("overall_status") == "reject" and not r.get("capa_note"))

col1.metric("QC runs entered", n_total)
col2.metric("Rejected runs", n_reject)
col3.metric("Warnings (1-2s)", n_warn)
col4.metric("⚠️ Rejects missing CAPA note", n_open_capa)

if n_open_capa:
    st.warning(
        f"There are **{n_open_capa}** rejected run(s) this month with no investigation note yet. "
        "Open **QC History** to document root cause / corrective action (CAPA) for traceability."
    )

st.divider()
st.subheader("Configured tests")
for test_id, t in defs.items():
    td = TestDefinition.from_dict(t)
    levels = ", ".join(
        f"{lv.control_name} (mean {lv.active_version().mean:g} ± {lv.active_version().sd:g})"
        for lv in td.levels.values() if lv.active_version()
    )
    st.markdown(f"**{td.test_name}** ({td.unit}) — {levels or 'no control levels defined yet'}")

st.divider()
st.caption(
    "Navigate using the sidebar: **Test Setup** → **QC Entry** → **Levey-Jennings** → "
    "**QC History** → **Reports**."
)
