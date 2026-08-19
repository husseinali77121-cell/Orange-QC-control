"""
core/auth.py
------------
Lightweight password gate + operator identification.

Kept intentionally simple (single shared QC access password, like the branch
passwords in HVMS/Send-Out) because the people entering QC are lab
technologists on shift, not per-branch accounts. What matters for ISO 15189
traceability is capturing WHO ran the QC and WHICH branch — not a heavy
login system — so we ask for an operator name + branch once per session.

Secrets used (see .streamlit/secrets.toml.example):
  qc_access_password : str
  branches           : list[str]   (defaults to Orange Lab's two branches)
"""

import streamlit as st

DEFAULT_BRANCHES = ["La Cité", "Diamond"]


def get_branches():
    try:
        branches = st.secrets.get("branches")
        if branches:
            return list(branches)
    except Exception:
        pass
    return DEFAULT_BRANCHES


def require_login() -> bool:
    """Renders a login form if needed. Returns True once the session is
    authenticated with an operator name + branch selected."""

    if st.session_state.get("qc_authed"):
        return True

    st.title("🧪 Orange Lab — BK-280 QC / Westgard")
    st.caption("Quality Control decision support for the BIOBASE BK-280")

    configured_password = None
    try:
        configured_password = st.secrets.get("qc_access_password")
    except Exception:
        pass

    with st.form("login_form"):
        operator = st.text_input("Operator name / اسم الفني المسؤول")
        branch = st.selectbox("Branch / الفرع", get_branches())
        pwd = None
        if configured_password:
            pwd = st.text_input("QC access password", type="password")
        submitted = st.form_submit_button("Enter / دخول")

    if submitted:
        if not operator.strip():
            st.error("Please enter your name / من فضلك اكتب الاسم")
            return False
        if configured_password and pwd != configured_password:
            st.error("Incorrect password / كلمة السر غير صحيحة")
            return False
        st.session_state["qc_authed"] = True
        st.session_state["qc_operator"] = operator.strip()
        st.session_state["qc_branch"] = branch
        st.rerun()

    return False


def current_operator() -> str:
    return st.session_state.get("qc_operator", "Unknown")


def current_branch() -> str:
    return st.session_state.get("qc_branch", get_branches()[0])


def logout_button():
    if st.sidebar.button("🚪 Logout / خروج"):
        for k in ("qc_authed", "qc_operator", "qc_branch"):
            st.session_state.pop(k, None)
        st.rerun()
