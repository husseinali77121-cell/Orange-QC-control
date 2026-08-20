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
  4) QC History          -> filterable audit log of every QC run + CAPA
  5) Reports             -> generate a printable PDF summary

Fixed after review:
  Every control level entered together is saved as its own record, so a
  single two-level run produced TWO rows — counting rows as "runs" (and
  counting a rejected LEVEL as a rejected RUN) overstated both numbers,
  e.g. a two-level run where both levels reject looked like "2 rejected
  runs" instead of 1. The dashboard now groups records back into runs by
  (test_id, date, run_number) before counting anything.
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
# Quick dashboard: current month's QC status across all tests, for this branch
# ---------------------------------------------------------------------------

today = dt.date.today()
yyyymm = today.strftime("%Y-%m")
records = data_manager.load_qc_records(yyyymm)
records = [r for r in records if r.get("branch") == auth.current_branch()]

# Group per-level records back into RUNS: (test_id, date, run_number).
runs = {}
for r in records:
    key = (r["test_id"], r["date"], r["run_number"])
    runs.setdefault(key, []).append(r)


def _run_worst_status(recs):
    if any(x["overall_status"] == "reject" for x in recs):
        return "reject"
    if any(x["overall_status"] == "warning" for x in recs):
        return "warning"
    return "in_control"


def _run_capa_open(recs):
    """A rejected run needs attention if ANY of its rejected level-records
    has no CAPA yet, or has a CAPA that isn't closed."""
    rejected = [x for x in recs if x["overall_status"] == "reject"]
    return any((x.get("capa") or {}).get("status") != "closed" for x in rejected)


st.subheader(f"📋 This month at a glance — {auth.current_branch()} ({yyyymm})")

n_total_runs = len(runs)
n_reject_runs = sum(1 for recs in runs.values() if _run_worst_status(recs) == "reject")
n_warn_runs = sum(1 for recs in runs.values() if _run_worst_status(recs) == "warning")
n_open_capa_runs = sum(1 for recs in runs.values() if _run_worst_status(recs) == "reject" and _run_capa_open(recs))

col1, col2, col3, col4 = st.columns(4)
col1.metric("QC runs entered", n_total_runs)
col2.metric("Rejected runs", n_reject_runs)
col3.metric("Warnings (1-2s)", n_warn_runs)
col4.metric("⚠️ Rejected runs — CAPA open", n_open_capa_runs)

if n_open_capa_runs:
    st.warning(
        f"There are **{n_open_capa_runs}** rejected run(s) this month with an open or missing "
        "investigation. Open **QC History** to document root cause / corrective action (CAPA)."
    )

# ---------------------------------------------------------------------------
# QC Health by Test — most recent run per active test, at a glance
# ---------------------------------------------------------------------------

st.divider()
st.subheader("🩺 QC Health by Test")

badge = {"in_control": "🟢", "warning": "🟡", "reject": "🔴"}
latest_run_per_test = {}
for (test_id, date, run_number), recs in runs.items():
    candidate = {"date": date, "run_number": run_number, "status": _run_worst_status(recs)}
    prev = latest_run_per_test.get(test_id)
    if not prev or (date, run_number) > (prev["date"], prev["run_number"]):
        latest_run_per_test[test_id] = candidate

for test_id, t in defs.items():
    if not t.get("active", True):
        continue
    info = latest_run_per_test.get(test_id)
    if info:
        st.markdown(
            f"{badge[info['status']]} **{t['test_name']}** — last run {info['date']} "
            f"(run {info['run_number']}): {info['status'].replace('_', ' ').upper()}"
        )
    else:
        st.markdown(f"⚪ **{t['test_name']}** — no QC entered yet this month")

rejected_runs = sorted(
    [(key, recs) for key, recs in runs.items() if _run_worst_status(recs) == "reject"],
    key=lambda kv: (kv[0][1], kv[0][2]), reverse=True
)[:5]
if rejected_runs:
    st.markdown("**Most recent rejected runs:**")
    for (test_id, date, run_number), recs in rejected_runs:
        rules = sorted({rid for x in recs for rid in x.get("violated_rules", [])})
        capa_flag = "🟢 CAPA closed" if not _run_capa_open(recs) else "🔴 CAPA open"
        test_name = defs.get(test_id, {}).get("test_name", test_id)
        st.caption(f"🔴 {date} · run {run_number} · {test_name} · rules: {', '.join(rules)} · {capa_flag}")

st.divider()
st.subheader("Configured tests")
for test_id, t in defs.items():
    td = TestDefinition.from_dict(t)
    levels = ", ".join(
        f"{lv.control_name} (mean {lv.active_version().mean:g} ± {lv.active_version().sd:g})"
        for lv in td.levels.values() if lv.active_version()
    )
    st.markdown(f"**{td.test_name}** ({td.unit}) — {levels or 'no control levels currently active'}")

st.divider()
st.caption(
    "Navigate using the sidebar: **Test Setup** → **QC Entry** → **Levey-Jennings** → "
    "**QC History** → **Reports**."
)
