"""
core/models.py
--------------
Data structures used across the Orange Lab QC / Westgard program.

Design notes
------------
- A `TestDefinition` describes an analyte (e.g. Glucose) run on the BK-280,
  with 1-3 control levels (Level 1 / 2 / 3). Each control level can have
  MULTIPLE `LevelVersion`s over time (new lot -> new mean/SD). This matters
  because a QC result entered in July must always be scored against the
  mean/SD that was active in July, even if the lot changes in August.
  We never retroactively rewrite history when a new lot is set up.

- A `QCPoint` is a single control result (one level, one run, one date)
  already carrying the mean/SD that was used to score it, plus the
  computed z-score. This is the unit the Westgard engine operates on.

- A `QCRecord` is the persisted form of a QCPoint plus bookkeeping fields
  (branch, operator, rule evaluation result, CAPA/investigation note).
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Optional, List, Dict, Any
import uuid


# ---------------------------------------------------------------------------
# Test / control level configuration
# ---------------------------------------------------------------------------

@dataclass
class LevelVersion:
    """One mean/SD 'era' for a control level (tied to a lot number)."""
    effective_from: str            # ISO date "YYYY-MM-DD"
    lot_number: str
    expiry_date: Optional[str]
    mean: float
    sd: float

    @property
    def cv_percent(self) -> float:
        if self.mean == 0:
            return 0.0
        return round((self.sd / self.mean) * 100, 2)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LevelVersion":
        return LevelVersion(**d)


@dataclass
class ControlLevel:
    """A control level (e.g. 'level_1') for a given test, with its history
    of mean/SD versions."""
    level_id: str                  # "level_1" / "level_2" / "level_3"
    control_name: str              # e.g. "Level 1 (Low)"
    versions: List[LevelVersion] = field(default_factory=list)

    def active_version(self, on_date: Optional[str] = None) -> Optional[LevelVersion]:
        """Return the version that was in effect on `on_date` (default: today).

        Returns None if no version had started yet by that date — e.g. the
        first lot is only effective from 2026-09-01 and `on_date` is
        2026-08-19. Callers MUST treat None as "no valid configuration for
        this date" (a configuration error to surface to the user), not
        silently fall back to a future lot's mean/SD — using a lot that
        didn't exist yet would score the QC result against the wrong
        reference and could hide a real error.
        """
        if not self.versions:
            return None
        target = on_date or date.today().isoformat()
        candidates = [v for v in self.versions if v.effective_from <= target]
        if not candidates:
            return None
        return sorted(candidates, key=lambda v: v.effective_from)[-1]

    def has_duplicate_effective_date(self, effective_from: str) -> bool:
        return any(v.effective_from == effective_from for v in self.versions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level_id": self.level_id,
            "control_name": self.control_name,
            "versions": [v.to_dict() for v in self.versions],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ControlLevel":
        return ControlLevel(
            level_id=d["level_id"],
            control_name=d["control_name"],
            versions=[LevelVersion.from_dict(v) for v in d.get("versions", [])],
        )


@dataclass
class TestDefinition:
    test_id: str                   # slug, e.g. "glucose"
    test_name: str                 # display name, e.g. "Glucose"
    unit: str                      # e.g. "mg/dL"
    method: str = ""                # e.g. "Hexokinase"
    analyzer: str = "BIOBASE BK-280"
    levels: Dict[str, ControlLevel] = field(default_factory=dict)
    active: bool = True
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id,
            "test_name": self.test_name,
            "unit": self.unit,
            "method": self.method,
            "analyzer": self.analyzer,
            "levels": {k: v.to_dict() for k, v in self.levels.items()},
            "active": self.active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "TestDefinition":
        return TestDefinition(
            test_id=d["test_id"],
            test_name=d["test_name"],
            unit=d.get("unit", ""),
            method=d.get("method", ""),
            analyzer=d.get("analyzer", "BIOBASE BK-280"),
            levels={k: ControlLevel.from_dict(v) for k, v in d.get("levels", {}).items()},
            active=d.get("active", True),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


# ---------------------------------------------------------------------------
# QC data points / records
# ---------------------------------------------------------------------------

@dataclass
class QCPoint:
    """The minimal unit the Westgard engine works with."""
    level_id: str
    date: str           # ISO "YYYY-MM-DD"
    run_number: int
    result: float
    mean: float
    sd: float
    record_id: Optional[str] = None

    @property
    def z(self) -> float:
        if not self.sd:
            return 0.0
        return (self.result - self.mean) / self.sd

    @property
    def sort_key(self):
        return (self.date, self.run_number)


@dataclass
class QCRecord:
    """Persisted QC entry, one per (test, level, run).

    `run_key` is the natural key used to detect duplicate submissions and
    to group records back into "runs" (a run may cover 1-3 levels):
    branch + test_id + date + run_number + level_id.

    `extended_rules_enabled` and `engine_version` are captured at entry
    time so a QC record stays auditable even if the rule set or the
    extended-rules setting changes later — you can always tell which
    ruleset actually produced a given historical verdict.

    `capa` holds the structured investigation/CAPA record for rejected
    runs (see core/data_manager.py). `capa_note` is kept only for backward
    compatibility with records written before the structured CAPA form
    existed; new code should read/write `capa`.

    `audit_events` is an append-only log of edits made to this record
    after it was first saved (e.g. CAPA updates) — old values are never
    silently overwritten without a trace.
    """
    id: str
    test_id: str
    test_name: str
    level_id: str
    control_name: str
    branch: str
    date: str
    run_number: int
    result: float
    unit: str
    mean_used: float
    sd_used: float
    lot_number: str
    operator: str
    overall_status: str            # "in_control" | "warning" | "reject"
    violated_rules: List[str] = field(default_factory=list)   # rule_ids
    extended_rules_enabled: bool = False
    engine_version: str = ""
    capa_note: str = ""             # deprecated — see `capa`
    capa: Optional[Dict[str, Any]] = None
    audit_events: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""

    @property
    def run_key(self) -> str:
        return f"{self.branch}|{self.test_id}|{self.date}|{self.run_number}|{self.level_id}"

    @property
    def z_score(self) -> float:
        if not self.sd_used:
            return 0.0
        return (self.result - self.mean_used) / self.sd_used

    def to_qc_point(self) -> QCPoint:
        return QCPoint(
            level_id=self.level_id,
            date=self.date,
            run_number=self.run_number,
            result=self.result,
            mean=self.mean_used,
            sd=self.sd_used,
            record_id=self.id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "QCRecord":
        return QCRecord(**d)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]
