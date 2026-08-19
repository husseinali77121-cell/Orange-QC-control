"""
pages/4_📋_QC_History.py
--------------------------
Filterable audit trail of every QC run, plus a place to document root
cause / corrective action (CAPA) for rejected runs — required for
ISO 15189-style traceability and useful when Dr. Tarek reviews the QC log.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from core import auth, data_manager

st.set_page_config(page_title="QC History — Orange Lab QC", page_icon="📋", layout="wide")

if not auth.require_login():
    st.stop()
auth.logout_button()

st.title("📋 QC History")

defs = data_manager.load_test_definitions().get("tests", {})

c1, c2, c3, c4 = st.columns(4)
date_from = c1.date_input("From", value=dt.date.today() - dt.timedelta(days=30))
date_to = c2.date_input("To", value=dt.date.today())
test_filter = c3.selectbox("Test", ["All"] + list(defs.keys()),
                            format_func=lambda k: "All" if k == "All" else defs[k]["test_name"])
status_filter = c4.selectbox("Status", ["All", "reject", "warning", "in_control"])


def months_between(d1: dt.date, d2: dt.date):
    months = []
    y, m = d1.year, d1.month
    while (y, m) <= (d2.year, d2.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m = 1
            y += 1
    return months


months = months_between(date_from, date_to)
records = data_manager.load_qc_records_range(months)
records = [r for r in records if date_from.isoformat() <= r["date"] <= date_to.isoformat()]
if test_filter != "All":
    records = [r for r in records if r["test_id"] == test_filter]
if status_filter != "All":
    records = [r for r in records if r["overall_status"] == status_filter]
records.sort(key=lambda r: (r["date"], r["run_number"]), reverse=True)

if not records:
    st.info("No QC records match this filter.")
    st.stop()

df = pd.DataFrame([{
    "Date": r["date"], "Run": r["run_number"], "Branch": r["branch"],
    "Test": r["test_name"], "Level": r["control_name"], "Result": r["result"],
    "Z": round((r["result"] - r["mean_used"]) / r["sd_used"], 2) if r["sd_used"] else 0,
    "Status": r["overall_status"].upper(),
    "Rule(s)": ", ".join(r.get("violated_rules", [])) or "-",
    "Operator": r["operator"],
    "CAPA note": r.get("capa_note", ""),
} for r in records])


def highlight(row):
    color = {"REJECT": "#fdecea", "WARNING": "#fff8e1"}.get(row["Status"], "")
    return [f"background-color: {color}"] * len(row)


st.dataframe(df.style.apply(highlight, axis=1), use_container_width=True, hide_index=True, height=420)
st.download_button("⬇️ Export as CSV", df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"orange_lab_qc_history_{date_from}_{date_to}.csv")

st.divider()
st.subheader("📝 Add investigation / CAPA note to a rejected run")

rejected = [r for r in records if r["overall_status"] == "reject"]
if not rejected:
    st.caption("No rejected runs in this filtered range.")
else:
    options = {
        f"{r['date']} · run {r['run_number']} · {r['test_name']} / {r['control_name']} "
        f"· {', '.join(r.get('violated_rules', []))}": r
        for r in rejected
    }
    choice = st.selectbox("Select rejected run", list(options.keys()))
    rec = options[choice]
    note = st.text_area("Root cause / corrective action", value=rec.get("capa_note", ""))
    if st.button("Save note"):
        yyyymm = rec["date"][:7]
        data_manager.update_qc_record(
            yyyymm, rec["id"], {"capa_note": note},
            commit_message=f"CAPA note: {rec['test_name']} {rec['date']} run {rec['run_number']}"
        )
        st.success("Note saved.")
        st.rerun()
