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
        """Return the version that was in effect on `on_date` (default: today)."""
        if not self.versions:
            return None
        target = on_date or date.today().isoformat()
        candidates = [v for v in self.versions if v.effective_from <= target]
        if not candidates:
            # everything is in the future relative to target -> fall back to earliest
            return sorted(self.versions, key=lambda v: v.effective_from)[0]
        return sorted(candidates, key=lambda v: v.effective_from)[-1]

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
    """Persisted QC entry, one per (test, level, run)."""
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
    capa_note: str = ""
    timestamp: str = ""

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
