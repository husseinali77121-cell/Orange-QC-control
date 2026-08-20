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

Fixed after review:
  - History fed to the engine is now filtered by BRANCH as well as test —
    previously two branches' independent QC could be treated as one
    continuous series, which could fire a false 2-2s/4-1s/10x.
  - History window is no longer a fixed "last 3 months" (too short for a
    low-frequency test to ever accumulate 10-12 points) — it now uses
    `data_manager.load_qc_history_for_level`, which searches backward
    until enough points are found.
  - Duplicate submissions (same branch/test/level/date/run entered twice,
    e.g. a double-click) are now blocked with a clear message.
  - Each saved record stores which rule set / engine version produced its
    verdict, and is skipped with a clear warning if no control lot was
    configured yet for the chosen date (instead of silently scoring
    against a lot that didn't exist yet on that date).
"""

import datetime as dt

import streamlit as st

from core import auth, data_manager
from core.models import TestDefinition, QCRecord, QCPoint
from core.westgard_engine import evaluate_run, ENGINE_VERSION

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

branch = auth.current_branch()

test_id = st.selectbox("Test", list(defs.keys()), format_func=lambda k: defs[k]["test_name"])
td = TestDefinition.from_dict(defs[test_id])

c1, c2 = st.columns(2)
run_date = c1.date_input("Date", value=dt.date.today())
extended_rules = c2.checkbox("Apply extended rules (8x / 9x / 12x / 7T)", value=False,
                              help="Off by default — matches the classic 6-rule Westgard multirule. "
                                   "Turn on only if your QC SOP specifies it.")

# ---------------------------------------------------------------------------
# Load enough history PER LEVEL, isolated to this branch, to safely evaluate
# every rule (including 10x/12x/7T which need up to 12 prior points).
# ---------------------------------------------------------------------------
history_by_level = {
    level_id: data_manager.load_qc_history_for_level(
        test_id=test_id, level_id=level_id, branch=branch, before_date=run_date.isoformat()
    )
    for level_id in td.levels
}
history_raw_all = [r for recs in history_by_level.values() for r in recs]

same_day_runs = sorted({
    r["run_number"] for recs in history_by_level.values() for r in recs if r["date"] == run_date.isoformat()
})
default_run = (same_day_runs[-1] + 1) if same_day_runs else 1
run_number = st.number_input("Run number", min_value=1, value=default_run, step=1)

st.markdown("### Enter control result(s) for this run")
level_values = {}
for level_id, lvl in td.levels.items():
    version = lvl.active_version(on_date=run_date.isoformat())
    if not version:
        st.warning(f"⚠️ No control lot is configured for **{lvl.control_name}** effective on "
                   f"{run_date.isoformat()} — check Test Setup. Skipped.")
        continue
    if version.expiry_date and version.expiry_date < run_date.isoformat():
        st.warning(f"⚠️ **{lvl.control_name}** lot `{version.lot_number}` expired on "
                   f"{version.expiry_date}. Verify control integrity before accepting this run.")

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

    # ---- duplicate guard: block re-submitting the exact same run ----
    duplicates = []
    for level_id in level_values:
        existing = data_manager.find_existing_record(
            test_id=test_id, level_id=level_id, branch=branch,
            date=run_date.isoformat(), run_number=int(run_number),
        )
        if existing:
            duplicates.append(td.levels[level_id].control_name)
    if duplicates:
        st.error(
            f"⛔ A QC record already exists for {branch} · {td.test_name} · run {run_number} · "
            f"{run_date.isoformat()} for: **{', '.join(duplicates)}**. "
            "Nothing was saved. If you need to correct a result, edit it from QC History instead "
            "of re-entering it."
        )
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
        for r in history_raw_all
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

    # ---- persist (this is the ONE place a verdict is computed; every
    # other page reads overall_status/violated_rules back, it never
    # recomputes them — see README architecture note) ----
    rule_ids_by_level = {}
    for v in evaluation.violations:
        for lid in v.levels_involved:
            rule_ids_by_level.setdefault(lid, []).append(v.rule_id)

    records_to_save = []
    for level_id, (val, version) in level_values.items():
        rec = QCRecord(
            id=QCRecord.new_id(),
            test_id=td.test_id, test_name=td.test_name,
            level_id=level_id, control_name=td.levels[level_id].control_name,
            branch=branch,
            date=run_date.isoformat(), run_number=int(run_number),
            result=val, unit=td.unit,
            mean_used=version.mean, sd_used=version.sd, lot_number=version.lot_number,
            operator=auth.current_operator(),
            overall_status=("reject" if any(
                v.status == "reject" for v in evaluation.violations if level_id in v.levels_involved
            ) else "warning" if any(
                v.status == "warning" for v in evaluation.violations if level_id in v.levels_involved
            ) else "in_control"),
            violated_rules=rule_ids_by_level.get(level_id, []),
            extended_rules_enabled=extended_rules,
            engine_version=ENGINE_VERSION,
            timestamp=dt.datetime.now().isoformat(),
        )
        records_to_save.append(rec.to_dict())

    yyyymm = run_date.strftime("%Y-%m")
    data_manager.append_qc_records(
        yyyymm, records_to_save,
        commit_message=f"QC entry: {td.test_name} run {run_number} on {run_date.isoformat()} ({branch})"
    )
    st.toast("Saved to QC log.", icon="✅")
