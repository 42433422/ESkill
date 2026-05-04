from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class TriggerPolicy:
    on_error: bool = True
    on_quality_below_threshold: bool = True
    force_dynamic: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "TriggerPolicy":
        raw = raw or {}
        return cls(
            on_error=bool(raw.get("on_error", True)),
            on_quality_below_threshold=bool(raw.get("on_quality_below_threshold", True)),
            force_dynamic=bool(raw.get("force_dynamic", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DynamicPatch:
    reason: str
    changes: dict[str, Any]
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DynamicPatch":
        return cls(
            reason=str(raw.get("reason") or "dynamic_adjustment"),
            changes=dict(raw.get("changes") or {}),
            created_at=str(raw.get("created_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SkillVersion:
    version: int
    static_logic: dict[str, Any]
    trigger_policy: TriggerPolicy = field(default_factory=TriggerPolicy)
    quality_gate: dict[str, Any] = field(default_factory=dict)
    source_run_id: str = ""
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SkillVersion":
        return cls(
            version=int(raw.get("version") or 1),
            static_logic=dict(raw.get("static_logic") or {}),
            trigger_policy=TriggerPolicy.from_dict(raw.get("trigger_policy")),
            quality_gate=dict(raw.get("quality_gate") or {}),
            source_run_id=str(raw.get("source_run_id") or ""),
            created_at=str(raw.get("created_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trigger_policy"] = self.trigger_policy.to_dict()
        return data


@dataclass(slots=True)
class ESkill:
    skill_id: str
    name: str
    domain: str
    active_version: int
    versions: list[SkillVersion] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    rollout: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ESkill":
        return cls(
            skill_id=str(raw.get("skill_id") or ""),
            name=str(raw.get("name") or ""),
            domain=str(raw.get("domain") or ""),
            active_version=int(raw.get("active_version") or 1),
            versions=[SkillVersion.from_dict(v) for v in raw.get("versions") or []],
            created_at=str(raw.get("created_at") or now_iso()),
            updated_at=str(raw.get("updated_at") or now_iso()),
            rollout=dict(raw.get("rollout") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "domain": self.domain,
            "active_version": self.active_version,
            "versions": [v.to_dict() for v in self.versions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "rollout": dict(self.rollout),
        }

    def get_active_version(self) -> SkillVersion:
        for version in self.versions:
            if version.version == self.active_version:
                return version
        if not self.versions:
            raise ValueError(f"Skill {self.skill_id} has no versions")
        return self.versions[-1]

    def add_version(self, version: SkillVersion, *, activate: bool = True) -> None:
        self.versions.append(version)
        if activate:
            self.active_version = version.version
        self.updated_at = now_iso()


@dataclass(slots=True)
class SkillRun:
    run_id: str
    skill_id: str
    stage: str
    input_data: dict[str, Any]
    output_data: dict[str, Any] = field(default_factory=dict)
    patch: DynamicPatch | None = None
    error: str = ""
    diagnosis: dict[str, Any] = field(default_factory=dict)
    analysis_report: dict[str, Any] = field(default_factory=dict)
    sandbox_summary: dict[str, Any] = field(default_factory=dict)
    rollout_phase: str = ""
    started_at: str = field(default_factory=now_iso)
    completed_at: str = ""

    def complete(self, output_data: dict[str, Any], stage: str | None = None) -> "SkillRun":
        self.output_data = output_data
        if stage:
            self.stage = stage
        self.completed_at = now_iso()
        return self

    def fail(self, error: str, stage: str | None = None) -> "SkillRun":
        self.error = error
        if stage:
            self.stage = stage
        self.completed_at = now_iso()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "skill_id": self.skill_id,
            "stage": self.stage,
            "input_data": self.input_data,
            "output_data": self.output_data,
            "patch": self.patch.to_dict() if self.patch else None,
            "error": self.error,
            "diagnosis": dict(self.diagnosis),
            "analysis_report": dict(self.analysis_report),
            "sandbox_summary": dict(self.sandbox_summary),
            "rollout_phase": self.rollout_phase,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(slots=True)
class ValidationReport:
    passed: bool
    score: float = 0.0
    issues: list[str] = field(default_factory=list)
    checked_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ValidationReport":
        return cls(
            passed=bool(raw.get("passed", False)),
            score=float(raw.get("score") or 0.0),
            issues=[str(x) for x in raw.get("issues") or []],
            checked_at=str(raw.get("checked_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvolutionEvent:
    skill_id: str
    event_type: str
    run_id: str = ""
    stage: str = ""
    trigger_signal: str = ""
    strategy: str = ""
    patch: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    solidified_version: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    diagnosis: dict[str, Any] | None = None
    analysis_report: dict[str, Any] | None = None
    sandbox_summary: dict[str, Any] | None = None
    rollout_phase: str = ""
    event_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvolutionEvent":
        return cls(
            skill_id=str(raw.get("skill_id") or ""),
            event_type=str(raw.get("event_type") or ""),
            run_id=str(raw.get("run_id") or ""),
            stage=str(raw.get("stage") or ""),
            trigger_signal=str(raw.get("trigger_signal") or ""),
            strategy=str(raw.get("strategy") or ""),
            patch=dict(raw["patch"]) if isinstance(raw.get("patch"), dict) else None,
            validation=dict(raw["validation"]) if isinstance(raw.get("validation"), dict) else None,
            solidified_version=(
                int(raw["solidified_version"]) if raw.get("solidified_version") is not None else None
            ),
            details=dict(raw.get("details") or {}),
            diagnosis=dict(raw["diagnosis"]) if isinstance(raw.get("diagnosis"), dict) else None,
            analysis_report=(
                dict(raw["analysis_report"]) if isinstance(raw.get("analysis_report"), dict) else None
            ),
            sandbox_summary=(
                dict(raw["sandbox_summary"]) if isinstance(raw.get("sandbox_summary"), dict) else None
            ),
            rollout_phase=str(raw.get("rollout_phase") or ""),
            event_id=str(raw.get("event_id") or uuid4().hex),
            created_at=str(raw.get("created_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("diagnosis", "analysis_report", "sandbox_summary"):
            if data.get(key) is None:
                del data[key]
        return data


@dataclass(slots=True)
class SkillHealthReport:
    skill_id: str
    suite_id: str
    passed: bool
    active_version: int
    checked_cases: int
    failed_cases: list[str] = field(default_factory=list)
    downgraded_to_version: int | None = None
    dependency_signals: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SkillHealthReport":
        return cls(
            skill_id=str(raw.get("skill_id") or ""),
            suite_id=str(raw.get("suite_id") or ""),
            passed=bool(raw.get("passed", False)),
            active_version=int(raw.get("active_version") or 0),
            checked_cases=int(raw.get("checked_cases") or 0),
            failed_cases=[str(x) for x in raw.get("failed_cases") or []],
            downgraded_to_version=(
                int(raw["downgraded_to_version"])
                if raw.get("downgraded_to_version") is not None
                else None
            ),
            dependency_signals=[
                dict(x) for x in raw.get("dependency_signals") or [] if isinstance(x, dict)
            ],
            created_at=str(raw.get("created_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AdaptivePolicyState:
    skill_id: str
    q_values: dict[str, float] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    successes: dict[str, int] = field(default_factory=dict)
    exploration_rate: float = 0.1
    updated_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AdaptivePolicyState":
        return cls(
            skill_id=str(raw.get("skill_id") or ""),
            q_values={str(k): float(v) for k, v in (raw.get("q_values") or {}).items()},
            attempts={str(k): int(v) for k, v in (raw.get("attempts") or {}).items()},
            successes={str(k): int(v) for k, v in (raw.get("successes") or {}).items()},
            exploration_rate=float(raw.get("exploration_rate") or 0.1),
            updated_at=str(raw.get("updated_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
