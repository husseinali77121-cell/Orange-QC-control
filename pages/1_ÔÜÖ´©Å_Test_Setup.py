"""
pages/1_⚙️_Test_Setup.py
-------------------------
Define tests (analytes) run on the BK-280 and their control levels.

Each control level keeps a HISTORY of mean/SD versions (one per lot). When
you set up a new lot, you add a new version with its own effective_from
date — old QC records keep scoring against the mean/SD that was actually
active when they were entered.

Fixed after review:
  - This page now sits behind an extra admin-password gate (see
    core/auth.require_admin) — every QC verdict downstream depends on the
    Mean/SD/lot values set here, so changing them deserves a higher bar
    than day-to-day QC entry.
  - Registering a new lot now rejects a duplicate effective_from date for
    the same level, and rejects an expiry date before the effective date
    — both were previously unvalidated.
  - The edit view now explicitly flags a level with NO active lot for
    today's date (e.g. every version is dated in the future) instead of
    silently showing nothing.
"""

import datetime as dt

import streamlit as st

from core import auth, data_manager
from core.models import TestDefinition, ControlLevel, LevelVersion

st.set_page_config(page_title="Test Setup — Orange Lab QC", page_icon="⚙️", layout="wide")

if not auth.require_login():
    st.stop()
auth.logout_button()

if not auth.require_admin():
    st.stop()

st.title("⚙️ Test Setup")
st.caption("Define analytes and control levels for the BIOBASE BK-280.")

defs = data_manager.load_test_definitions().get("tests", {})

tab_new, tab_edit, tab_lot = st.tabs(["➕ New test", "✏️ View / edit tests", "🧪 New lot for existing level"])

# ---------------------------------------------------------------------------
# New test
# ---------------------------------------------------------------------------
with tab_new:
    st.subheader("Create a new test")
    with st.form("new_test_form"):
        c1, c2, c3 = st.columns(3)
        test_name = c1.text_input("Test name (e.g. Glucose)")
        unit = c2.text_input("Unit (e.g. mg/dL)")
        method = c3.text_input("Method (optional)")

        n_levels = st.radio("Number of control levels", [1, 2, 3], index=1, horizontal=True)

        st.markdown("**Control level details**")
        level_inputs = []
        default_names = ["Level 1 (Low)", "Level 2 (Normal)", "Level 3 (High)"]
        for i in range(n_levels):
            with st.expander(default_names[i], expanded=True):
                lc1, lc2, lc3, lc4 = st.columns(4)
                lot = lc1.text_input("Lot number", key=f"lot_{i}")
                expiry = lc2.date_input("Expiry date", key=f"exp_{i}", value=None)
                mean = lc3.number_input("Mean", key=f"mean_{i}", step=0.01, format="%.3f")
                sd = lc4.number_input("SD", key=f"sd_{i}", step=0.001, format="%.4f")
                level_inputs.append({"name": default_names[i], "lot": lot, "expiry": expiry,
                                      "mean": mean, "sd": sd})

        effective_from = st.date_input("Effective from", value=dt.date.today())
        submitted = st.form_submit_button("Save test")

    if submitted:
        bad_expiry = [
            li["name"] for li in level_inputs
            if li["expiry"] and li["expiry"] < effective_from
        ]
        if not test_name.strip():
            st.error("Test name is required.")
        elif any(li["sd"] <= 0 for li in level_inputs):
            st.error("SD must be greater than 0 for every level (needed for Z-score calculation).")
        elif bad_expiry:
            st.error(f"Expiry date is before the effective date for: {', '.join(bad_expiry)}. "
                     "Fix the dates and try again.")
        else:
            test_id = test_name.strip().lower().replace(" ", "_")
            levels = {}
            for i, li in enumerate(level_inputs):
                level_id = f"level_{i+1}"
                version = LevelVersion(
                    effective_from=effective_from.isoformat(),
                    lot_number=li["lot"] or "N/A",
                    expiry_date=li["expiry"].isoformat() if li["expiry"] else None,
                    mean=li["mean"],
                    sd=li["sd"],
                )
                levels[level_id] = ControlLevel(level_id=level_id, control_name=li["name"], versions=[version])

            td = TestDefinition(
                test_id=test_id, test_name=test_name.strip(), unit=unit.strip(), method=method.strip(),
                levels=levels, active=True,
                created_at=dt.datetime.now().isoformat(), updated_at=dt.datetime.now().isoformat(),
            )
            data_manager.save_test_definition(td.to_dict(), commit_message=f"Add test: {test_name}")
            st.success(f"Saved **{test_name}** with {n_levels} control level(s).")
            st.rerun()

# ---------------------------------------------------------------------------
# View / edit
# ---------------------------------------------------------------------------
with tab_edit:
    if not defs:
        st.info("No tests configured yet.")
    for test_id, t in defs.items():
        td = TestDefinition.from_dict(t)
        with st.expander(f"{td.test_name} ({td.unit})"):
            for level_id, lvl in td.levels.items():
                active = lvl.active_version()
                st.markdown(f"**{lvl.control_name}**")
                if active:
                    st.write(
                        f"Active lot: `{active.lot_number}` · Mean: {active.mean:g} · "
                        f"SD: {active.sd:g} · CV%: {active.cv_percent}% · "
                        f"Effective from: {active.effective_from}"
                        + (f" · Expires: {active.expiry_date}" if active.expiry_date else "")
                    )
                elif lvl.versions:
                    next_start = sorted(v.effective_from for v in lvl.versions)[0]
                    st.error(f"⚠️ No lot is active for today — the earliest configured lot "
                             f"doesn't start until {next_start}. QC Entry will skip this level "
                             f"until then.")
                else:
                    st.warning("No lot configured yet for this level.")
                if len(lvl.versions) > 1:
                    st.caption(f"{len(lvl.versions)} lot versions on record (history preserved for scoring old QC).")
            active_toggle = st.checkbox("Active", value=td.active, key=f"active_{test_id}")
            if active_toggle != td.active:
                td.active = active_toggle
                td.updated_at = dt.datetime.now().isoformat()
                data_manager.save_test_definition(td.to_dict(), commit_message=f"Update active flag: {td.test_name}")
                st.rerun()

# ---------------------------------------------------------------------------
# New lot version for an existing level
# ---------------------------------------------------------------------------
with tab_lot:
    if not defs:
        st.info("No tests configured yet.")
    else:
        st.subheader("Register a new control lot")
        st.caption(
            "Use this when you open a new control lot / recalibrate. It keeps the OLD mean/SD "
            "for scoring past QC results, and applies the NEW mean/SD from the effective date onward."
        )
        test_id = st.selectbox("Test", list(defs.keys()), format_func=lambda k: defs[k]["test_name"])
        td = TestDefinition.from_dict(defs[test_id])
        level_id = st.selectbox("Control level", list(td.levels.keys()),
                                 format_func=lambda k: td.levels[k].control_name)

        with st.form("new_lot_form"):
            c1, c2, c3, c4 = st.columns(4)
            lot = c1.text_input("New lot number")
            expiry = c2.date_input("Expiry date", value=None)
            mean = c3.number_input("New mean", step=0.01, format="%.3f")
            sd = c4.number_input("New SD", step=0.001, format="%.4f")
            eff = st.date_input("Effective from", value=dt.date.today())
            submitted_lot = st.form_submit_button("Add lot version")

        if submitted_lot:
            if sd <= 0:
                st.error("SD must be greater than 0.")
            elif expiry and expiry < eff:
                st.error("Expiry date can't be before the effective date.")
            elif td.levels[level_id].has_duplicate_effective_date(eff.isoformat()):
                st.error(
                    f"A lot version for {td.levels[level_id].control_name} is already effective "
                    f"from {eff.isoformat()}. Pick a different effective date, or edit that entry "
                    "directly in the data file if this was a mistake."
                )
            else:
                version = LevelVersion(
                    effective_from=eff.isoformat(), lot_number=lot or "N/A",
                    expiry_date=expiry.isoformat() if expiry else None, mean=mean, sd=sd,
                )
                td.levels[level_id].versions.append(version)
                td.updated_at = dt.datetime.now().isoformat()
                data_manager.save_test_definition(
                    td.to_dict(), commit_message=f"New lot for {td.test_name}/{level_id}: {lot}"
                )
                st.success(f"New lot `{lot}` added for {td.levels[level_id].control_name}, effective {eff}.")
                st.rerun()
