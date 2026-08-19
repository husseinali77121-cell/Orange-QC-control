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
import json
import os
import time
from typing import Any, Dict, List, Optional

import requests
import streamlit as st

GITHUB_API = "https://api.github.com"
LOCAL_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


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


def update_qc_record(yyyymm: str, record_id: str, patch: Dict[str, Any], commit_message: str) -> None:
    """Used for CAPA notes / investigation follow-up on an existing record."""
    path = _month_path(yyyymm)

    def mutate(content):
        content = content or {"records": []}
        for r in content.get("records", []):
            if r["id"] == record_id:
                r.update(patch)
        return content

    if _github_configured():
        _gh_write_with_retry(path, mutate, commit_message)
    else:
        current = _local_read_or_default(path, {"records": []})
        current = mutate(current)
        _local_write(path, current)


def storage_mode() -> str:
    return "GitHub" if _github_configured() else "Local (offline/dev)"
