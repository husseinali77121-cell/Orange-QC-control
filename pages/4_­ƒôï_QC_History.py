"""
pages/4_📋_QC_History.py
--------------------------
Filterable audit trail of every QC run, plus a structured investigation
(CAPA) workflow for rejected runs — required for ISO 15189-style
traceability and useful when Dr. Tarek reviews the QC log.

Fixed after review:
  - CAPA used to be a single free-text box that got overwritten with no
    trace of the previous value. It's now a structured record (incident
    status, immediate action, root cause, corrective action, preventive
    action, recheck QC, responsible person, opened/closed by+when), and
    every edit is captured in an audit trail (see
    core/data_manager.update_qc_record) instead of silently replacing the
    old value.
  - Uses the centralized data_manager.months_between() instead of a
    page-local copy, so this can't silently drift from the other pages'
    date-range logic.
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
branch_filter = st.selectbox("Branch", ["All"] + auth.get_branches())

months = data_manager.months_between(date_from.isoformat(), date_to.isoformat())
records = data_manager.load_qc_records_range(months)
records = [r for r in records if date_from.isoformat() <= r["date"] <= date_to.isoformat()]
if test_filter != "All":
    records = [r for r in records if r["test_id"] == test_filter]
if status_filter != "All":
    records = [r for r in records if r["overall_status"] == status_filter]
if branch_filter != "All":
    records = [r for r in records if r["branch"] == branch_filter]
records.sort(key=lambda r: (r["date"], r["run_number"]), reverse=True)

if not records:
    st.info("No QC records match this filter.")
    st.stop()


def _capa_status(r):
    capa = r.get("capa")
    if capa:
        return capa.get("status", "open").upper()
    if r.get("capa_note"):  # legacy free-text note from before this workflow existed
        return "NOTE (legacy)"
    return "-"


df = pd.DataFrame([{
    "Date": r["date"], "Run": r["run_number"], "Branch": r["branch"],
    "Test": r["test_name"], "Level": r["control_name"], "Result": r["result"],
    "Z": round((r["result"] - r["mean_used"]) / r["sd_used"], 2) if r["sd_used"] else 0,
    "Status": r["overall_status"].upper(),
    "Rule(s)": ", ".join(r.get("violated_rules", [])) or "-",
    "Operator": r["operator"],
    "CAPA": _capa_status(r),
} for r in records])


def highlight(row):
    color = {"REJECT": "#fdecea", "WARNING": "#fff8e1"}.get(row["Status"], "")
    return [f"background-color: {color}"] * len(row)


st.dataframe(df.style.apply(highlight, axis=1), use_container_width=True, hide_index=True, height=420)
st.download_button("⬇️ Export as CSV", df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"orange_lab_qc_history_{date_from}_{date_to}.csv")

st.divider()
st.subheader("📝 Investigation / CAPA for a rejected run")

rejected = [r for r in records if r["overall_status"] == "reject"]
if not rejected:
    st.caption("No rejected runs in this filtered range.")
else:
    def _label(r):
        capa_flag = "🟢" if (r.get("capa") or {}).get("status") == "closed" else "🔴"
        return (f"{capa_flag} {r['date']} · run {r['run_number']} · {r['branch']} · "
                f"{r['test_name']} / {r['control_name']} · {', '.join(r.get('violated_rules', []))}")

    options = {_label(r): r for r in rejected}
    choice = st.selectbox("Select rejected run", list(options.keys()))
    rec = options[choice]
    existing_capa = rec.get("capa") or {}

    with st.form("capa_form"):
        cc1, cc2 = st.columns(2)
        status = cc1.selectbox("Status", ["open", "investigating", "closed"],
                                index=["open", "investigating", "closed"].index(existing_capa.get("status", "open")))
        responsible = cc2.text_input("Responsible person", value=existing_capa.get("responsible_person", ""))
        immediate_action = st.text_area("Immediate action taken", value=existing_capa.get("immediate_action", ""))
        root_cause = st.text_area("Root cause", value=existing_capa.get("root_cause", ""))
        corrective_action = st.text_area("Corrective action", value=existing_capa.get("corrective_action", ""))
        preventive_action = st.text_area("Preventive action", value=existing_capa.get("preventive_action", ""))
        recheck_qc = st.text_input("Recheck QC result (e.g. \"Repeated 2026-08-20 run 1 — IN CONTROL\")",
                                    value=existing_capa.get("recheck_qc", ""))
        save_capa = st.form_submit_button("💾 Save investigation")

    if save_capa:
        now = dt.datetime.now().isoformat()
        capa = {
            "incident_id": existing_capa.get("incident_id", f"CAPA-{rec['id']}"),
            "status": status,
            "responsible_person": responsible,
            "immediate_action": immediate_action,
            "root_cause": root_cause,
            "corrective_action": corrective_action,
            "preventive_action": preventive_action,
            "recheck_qc": recheck_qc,
            "opened_by": existing_capa.get("opened_by", auth.current_operator()),
            "opened_at": existing_capa.get("opened_at", now),
            "closed_by": auth.current_operator() if status == "closed" else existing_capa.get("closed_by", ""),
            "closed_at": now if status == "closed" else existing_capa.get("closed_at", ""),
        }
        yyyymm = rec["date"][:7]
        data_manager.update_qc_record(
            yyyymm, rec["id"], {"capa": capa},
            commit_message=f"CAPA update: {rec['test_name']} {rec['date']} run {rec['run_number']} -> {status}",
            actor=auth.current_operator(),
        )
        st.success("Investigation saved.")
        st.rerun()

    audit_events = rec.get("audit_events", [])
    if audit_events:
        with st.expander(f"🕓 Change history ({len(audit_events)})"):
            for ev in sorted(audit_events, key=lambda e: e["timestamp"], reverse=True):
                st.markdown(
                    f"**{ev['timestamp'][:19].replace('T', ' ')}** · {ev['actor']} changed **{ev['field']}**"
                )
                st.caption(f"Before: {ev['old_value']}")
                st.caption(f"After: {ev['new_value']}")
                st.markdown("---")
