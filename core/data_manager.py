"""
core/data_manager.py
---------------------
Persistence layer for the QC program.

Two modes, auto-selected:

1) GITHUB MODE (production — same pattern as HVMS / Send-Out System):
   Reads/writes JSON files through the GitHub Contents API using
   st.secrets["github_token"] / ["github_repo"] / ["github_branch"].
   Uses the file SHA for conditional updates and retries once on a 409
   (someone else wrote in between) by re-fetching the SHA — same idea as
   the Send-Out System's retry-on-409 handling.

2) LOCAL MODE (dev/testing over Termux without touching GitHub):
   If github secrets are not configured, falls back to plain JSON files
   under ./data/. Lets you test the whole app offline before wiring it to
   your data repo.

Storage layout (mirrors HVMS's monthly-partition idea, schema-lite):
  data/test_definitions.json          -> all TestDefinition configs (small, rarely changes)
  data/qc_records_YYYY-MM.json        -> QCRecords for that month (append-heavy)

Only JSON, base64-encoded for GitHub Contents API as required by GitHub.
"""

import base64
import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional

import requests
import streamlit as st

from core.date_utils import last_n_months, months_between  # noqa: F401  (re-exported for pages)

GITHUB_API = "https://api.github.com"
LOCAL_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# NOTE: last_n_months / months_between now live in core/date_utils.py
# (pure functions, unit tested there) and are imported above so existing
# call sites like `data_manager.last_n_months(...)` still work unchanged.


def _github_configured() -> bool:
    try:
        return bool(st.secrets.get("github_token")) and bool(st.secrets.get("github_repo"))
    except Exception:
        return False


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"token {st.secrets['github_token']}",
        "Accept": "application/vnd.github+json",
    }


def _repo() -> str:
    return st.secrets["github_repo"]


def _branch() -> str:
    return st.secrets.get("github_branch", "main")


# ---------------------------------------------------------------------------
# Low-level GitHub Contents API helpers
# ---------------------------------------------------------------------------

def _gh_get_file(path: str) -> Optional[Dict[str, Any]]:
    """Returns {"content": dict_or_list, "sha": str} or None if the file doesn't exist yet."""
    url = f"{GITHUB_API}/repos/{_repo()}/contents/{path}"
    resp = requests.get(url, headers=_headers(), params={"ref": _branch()}, timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    payload = resp.json()
    raw = base64.b64decode(payload["content"]).decode("utf-8")
    return {"content": json.loads(raw), "sha": payload["sha"]}


def _gh_put_file(path: str, data: Any, message: str, sha: Optional[str]) -> None:
    url = f"{GITHUB_API}/repos/{_repo()}/contents/{path}"
    body = {
        "message": message,
        "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii"),
        "branch": _branch(),
    }
    if sha:
        body["sha"] = sha
    resp = requests.put(url, headers=_headers(), json=body, timeout=15)
    if resp.status_code == 409:
        raise _ConflictError()
    resp.raise_for_status()


class _ConflictError(Exception):
    pass


def _gh_read_or_default(path: str, default: Any) -> Any:
    got = _gh_get_file(path)
    return got["content"] if got else default


def _gh_write_with_retry(path: str, mutate_fn, message: str, max_retries: int = 3) -> Any:
    """
    Read-modify-write with retry on conflict, mirroring the Send-Out System's
    retry-on-409 pattern:
      1. fetch current content + sha
      2. apply mutate_fn(content) -> new_content
      3. try to PUT with that sha
      4. on 409, re-fetch and retry
    """
    last_err = None
    for attempt in range(max_retries):
        got = _gh_get_file(path)
        content = got["content"] if got else None
        sha = got["sha"] if got else None
        new_content = mutate_fn(content)
        try:
            _gh_put_file(path, new_content, message, sha)
            return new_content
        except _ConflictError as e:
            last_err = e
            time.sleep(0.4 * (attempt + 1))
            continue
    raise RuntimeError(f"Could not write {path} after {max_retries} retries (conflicts).") from last_err


# ---------------------------------------------------------------------------
# Local-mode helpers
# ---------------------------------------------------------------------------

def _local_path(rel_path: str) -> str:
    return os.path.join(LOCAL_DATA_DIR, rel_path)


def _local_read_or_default(rel_path: str, default: Any) -> Any:
    p = _local_path(rel_path)
    if not os.path.exists(p):
        return default
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _local_write(rel_path: str, data: Any) -> None:
    p = _local_path(rel_path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Public API — test definitions
# ---------------------------------------------------------------------------

TESTS_PATH = "data/test_definitions.json"


def load_test_definitions() -> Dict[str, Any]:
    default = {"tests": {}}
    if _github_configured():
        return _gh_read_or_default(TESTS_PATH, default)
    return _local_read_or_default(TESTS_PATH, default)


def save_test_definition(test_dict: Dict[str, Any], commit_message: str) -> None:
    def mutate(content):
        content = content or {"tests": {}}
        content.setdefault("tests", {})[test_dict["test_id"]] = test_dict
        return content

    if _github_configured():
        _gh_write_with_retry(TESTS_PATH, mutate, commit_message)
    else:
        current = _local_read_or_default(TESTS_PATH, {"tests": {}})
        current = mutate(current)
        _local_write(TESTS_PATH, current)


# ---------------------------------------------------------------------------
# Public API — QC records (monthly partitioned)
# ---------------------------------------------------------------------------

def _month_path(yyyymm: str) -> str:
    return f"data/qc_records_{yyyymm}.json"


def load_qc_records(yyyymm: str) -> List[Dict[str, Any]]:
    default: Dict[str, Any] = {"records": []}
    path = _month_path(yyyymm)
    if _github_configured():
        content = _gh_read_or_default(path, default)
    else:
        content = _local_read_or_default(path, default)
    return content.get("records", [])


def load_qc_records_range(months: List[str]) -> List[Dict[str, Any]]:
    """Convenience: load and concatenate several YYYY-MM partitions."""
    out: List[Dict[str, Any]] = []
    for m in months:
        out.extend(load_qc_records(m))
    return out


def append_qc_records(yyyymm: str, new_records: List[Dict[str, Any]], commit_message: str) -> None:
    path = _month_path(yyyymm)

    def mutate(content):
        content = content or {"records": []}
        content.setdefault("records", [])
        content["records"].extend(new_records)
        return content

    if _github_configured():
        _gh_write_with_retry(path, mutate, commit_message)
    else:
        current = _local_read_or_default(path, {"records": []})
        current = mutate(current)
        _local_write(path, current)


def update_qc_record(
    yyyymm: str, record_id: str, patch: Dict[str, Any], commit_message: str, actor: str = "Unknown"
) -> None:
    """Used for CAPA notes / investigation follow-up on an existing record.

    Never silently overwrites: for every field in `patch` whose value
    actually changes, an entry is appended to the record's `audit_events`
    list (timestamp, actor, field, old_value, new_value) before the patch
    is applied. This gives a real audit trail instead of losing the
    previous value the moment someone edits a CAPA note.
    """
    path = _month_path(yyyymm)

    def mutate(content):
        content = content or {"records": []}
        for r in content.get("records", []):
            if r["id"] == record_id:
                audit = r.setdefault("audit_events", [])
                for key, new_val in patch.items():
                    old_val = r.get(key)
                    if old_val != new_val:
                        audit.append({
                            "timestamp": dt.datetime.now().isoformat(),
                            "actor": actor,
                            "field": key,
                            "old_value": old_val,
                            "new_value": new_val,
                        })
                r.update(patch)
        return content

    if _github_configured():
        _gh_write_with_retry(path, mutate, commit_message)
    else:
        current = _local_read_or_default(path, {"records": []})
        current = mutate(current)
        _local_write(path, current)


def find_existing_record(
    test_id: str, level_id: str, branch: str, date: str, run_number: int
) -> Optional[Dict[str, Any]]:
    """Exact-match lookup used to block duplicate QC submissions (same
    branch + test + level + date + run entered twice, e.g. a double-click
    on Save). Only checks the relevant month partition."""
    for r in load_qc_records(date[:7]):
        if (r["test_id"] == test_id and r["level_id"] == level_id and r["branch"] == branch
                and r["date"] == date and r["run_number"] == run_number):
            return r
    return None


def load_qc_history_for_level(
    test_id: str,
    level_id: str,
    branch: str,
    before_date: Optional[str] = None,
    min_points: int = 15,
    max_months_back: int = 36,
    max_empty_streak: int = 6,
) -> List[Dict[str, Any]]:
    """
    Load enough QC history for one exact (test, level, branch) to safely
    evaluate every Westgard rule the engine supports — including 12x / 7T,
    which need up to 12 prior points. A fixed "last 3 calendar months"
    window silently under-counts for low-frequency analytes (e.g. a test
    run only a few times a month could take most of a year to reach 10
    points). Instead this walks backward month by month, filtering
    strictly by branch + level, accumulating matches until either:
      - `min_points` matching records have been collected, or
      - `max_months_back` months have been scanned, or
      - `max_empty_streak` consecutive months had zero matches (assume QC
        simply wasn't running that far back, to avoid scanning years of
        empty files for a brand-new test).
    Returns records sorted chronologically (oldest first) — ready to feed
    straight into the Westgard engine as `history`.
    """
    ref = before_date or dt.date.today().isoformat()
    collected: List[Dict[str, Any]] = []
    empty_streak = 0

    for yyyymm in last_n_months(max_months_back, ref):
        matched = [
            r for r in load_qc_records(yyyymm)
            if r["test_id"] == test_id and r["level_id"] == level_id and r["branch"] == branch
        ]
        if matched:
            collected.extend(matched)
            empty_streak = 0
        else:
            empty_streak += 1

        if len(collected) >= min_points or empty_streak >= max_empty_streak:
            break

    collected.sort(key=lambda r: (r["date"], r["run_number"]))
    return collected


def storage_mode() -> str:
    return "GitHub" if _github_configured() else "Local (offline/dev)"
