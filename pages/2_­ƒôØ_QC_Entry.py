"""
pages/2_📝_QC_Entry.py
------------------------
Enter today's control result(s) and get an INSTANT, explained Westgard
verdict — not just a rule code:

    Status: REJECT
    Violated Rule: 1-3s
    Interpretation: Random error suspected
    Recommended Action: Do not release patient results. Investigate ...

If you enter both Level 1 and Level 2 for the same run, within-run rules
(2-2s within-run, R-4s) are checked too.
"""

import datetime as dt

import streamlit as st

from core import auth, data_manager
from core.models import TestDefinition, QCRecord, QCPoint
from core.westgard_engine import evaluate_run

st.set_page_config(page_title="QC Entry — Orange Lab QC", page_icon="📝", layout="wide")

if not auth.require_login():
    st.stop()
auth.logout_button()

st.title("📝 QC Entry")

defs = data_manager.load_test_definitions().get("tests", {})
defs = {k: v for k, v in defs.items() if v.get("active", True)}
if not defs:
    st.info("No active tests configured. Add one on the **Test Setup** page first.")
    st.stop()


def last_n_months(n: int, from_date: dt.date):
    out = []
    y, m = from_date.year, from_date.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


test_id = st.selectbox("Test", list(defs.keys()), format_func=lambda k: defs[k]["test_name"])
td = TestDefinition.from_dict(defs[test_id])

c1, c2 = st.columns(2)
run_date = c1.date_input("Date", value=dt.date.today())
extended_rules = c2.checkbox("Apply extended rules (8x / 9x / 12x / 7T)", value=False,
                              help="Off by default — matches the classic 6-rule Westgard multirule. "
                                   "Turn on only if your QC SOP specifies it.")

# figure out a sensible default run number
months_ctx = last_n_months(3, run_date)
history_raw = data_manager.load_qc_records_range(months_ctx)
history_raw = [r for r in history_raw if r["test_id"] == test_id]
same_day_runs = sorted({r["run_number"] for r in history_raw if r["date"] == run_date.isoformat()})
default_run = (same_day_runs[-1] + 1) if same_day_runs else 1
run_number = st.number_input("Run number", min_value=1, value=default_run, step=1)

st.markdown("### Enter control result(s) for this run")
level_values = {}
for level_id, lvl in td.levels.items():
    version = lvl.active_version(on_date=run_date.isoformat())
    if not version:
        continue
    cols = st.columns([2, 1, 1])
    include = cols[0].checkbox(f"{lvl.control_name}  ·  lot {version.lot_number}  ·  "
                                f"mean {version.mean:g} ± {version.sd:g}", value=True,
                                key=f"inc_{level_id}")
    val = cols[1].number_input("Result", key=f"val_{level_id}", step=0.01, format="%.3f",
                                label_visibility="collapsed")
    if include:
        level_values[level_id] = (val, version)

submitted = st.button("🔎 Evaluate & Save", type="primary", use_container_width=True)

if submitted:
    if not level_values:
        st.error("Select at least one control level and enter a result.")
        st.stop()

    new_points = []
    meta = {}
    for level_id, (val, version) in level_values.items():
        p = QCPoint(level_id=level_id, date=run_date.isoformat(), run_number=int(run_number),
                    result=val, mean=version.mean, sd=version.sd)
        new_points.append(p)
        meta[level_id] = version

    history_points = [
        QCPoint(level_id=r["level_id"], date=r["date"], run_number=r["run_number"],
                result=r["result"], mean=r["mean_used"], sd=r["sd_used"], record_id=r["id"])
        for r in history_raw
    ]

    evaluation = evaluate_run(new_points, history_points, extended_rules=extended_rules)

    # ---- render verdict ----
    if evaluation.overall_status == "reject":
        st.error("### 🔴 Status: REJECT — do not release patient results")
    elif evaluation.overall_status == "warning":
        st.warning("### 🟡 Status: WARNING — review before releasing")
    else:
        st.success("### 🟢 Status: IN CONTROL")

    for lid, z in evaluation.per_level_z.items():
        st.caption(f"{td.levels[lid].control_name}: result = {level_values[lid][0]:g} → Z = {z:+.2f}")

    if evaluation.violations:
        for v in evaluation.violations:
            icon = "🔴" if v.status == "reject" else "🟡"
            with st.container(border=True):
                st.markdown(f"{icon} **Violated Rule: {v.rule_name}**  "
                            f"({'within-run' if v.scope == 'within_run' else 'across-run'})")
                st.markdown(f"**Error type:** {v.error_type}")
                st.markdown(f"**Interpretation:** {v.interpretation}")
                st.markdown(f"**Recommended action:** {v.action}")
    else:
        st.info("No Westgard rule violated. Run accepted — patient results may be released.")

    # ---- persist ----
    rule_names_by_level = {}
    for v in evaluation.violations:
        for lid in v.levels_involved:
            rule_names_by_level.setdefault(lid, []).append(v.rule_id)

    records_to_save = []
    for level_id, (val, version) in level_values.items():
        rec = QCRecord(
            id=QCRecord.new_id(),
            test_id=td.test_id, test_name=td.test_name,
            level_id=level_id, control_name=td.levels[level_id].control_name,
            branch=auth.current_branch(),
            date=run_date.isoformat(), run_number=int(run_number),
            result=val, unit=td.unit,
            mean_used=version.mean, sd_used=version.sd, lot_number=version.lot_number,
            operator=auth.current_operator(),
            overall_status=("reject" if any(
                v.status == "reject" for v in evaluation.violations if level_id in v.levels_involved
            ) else "warning" if any(
                v.status == "warning" for v in evaluation.violations if level_id in v.levels_involved
            ) else "in_control"),
            violated_rules=rule_names_by_level.get(level_id, []),
            timestamp=dt.datetime.now().isoformat(),
        )
        records_to_save.append(rec.to_dict())

    yyyymm = run_date.strftime("%Y-%m")
    data_manager.append_qc_records(
        yyyymm, records_to_save,
        commit_message=f"QC entry: {td.test_name} run {run_number} on {run_date.isoformat()}"
    )
    st.toast("Saved to QC log.", icon="✅")
