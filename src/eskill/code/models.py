"""Data models for code-layer self-healing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..models import TriggerPolicy, now_iso


@dataclass(slots=True)
class CodeFunctionSignature:
    """Function signature — fixes must preserve interface."""

    params: list[str]
    return_type: str
    required_params: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CodeFunctionSignature:
        return cls(
            params=[str(x) for x in raw.get("params") or []],
            return_type=str(raw.get("return_type") or "dict"),
            required_params=[str(x) for x in raw.get("required_params") or []],
        )


@dataclass(slots=True)
class CodeTestCase:
    """Built-in tests — patched code must pass all."""

    case_id: str
    input_data: dict[str, Any]
    expected_output: dict[str, Any] | None = None
    assert_fn: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CodeTestCase:
        eo = raw.get("expected_output")
        return cls(
            case_id=str(raw.get("case_id") or raw.get("id") or "case"),
            input_data=dict(raw.get("input_data") or {}),
            expected_output=dict(eo) if isinstance(eo, dict) else None,
            assert_fn=str(raw["assert_fn"]) if raw.get("assert_fn") else None,
        )


@dataclass(slots=True)
class CodeSkillVersion:
    version: int
    source_code: str
    function_name: str
    signature: CodeFunctionSignature
    dependencies: list[str] = field(default_factory=list)
    trigger_policy: TriggerPolicy = field(default_factory=TriggerPolicy)
    quality_gate: dict[str, Any] = field(default_factory=dict)
    test_cases: list[CodeTestCase] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    source_run_id: str = ""
    domain_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_code": self.source_code,
            "function_name": self.function_name,
            "signature": self.signature.to_dict(),
            "dependencies": list(self.dependencies),
            "trigger_policy": self.trigger_policy.to_dict(),
            "quality_gate": dict(self.quality_gate),
            "test_cases": [tc.to_dict() for tc in self.test_cases],
            "created_at": self.created_at,
            "source_run_id": self.source_run_id,
            "domain_keywords": list(self.domain_keywords),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CodeSkillVersion:
        tcs = raw.get("test_cases") or []
        cases = [CodeTestCase.from_dict(x) for x in tcs if isinstance(x, dict)]
        return cls(
            version=int(raw.get("version") or 1),
            source_code=str(raw.get("source_code") or ""),
            function_name=str(raw.get("function_name") or "run"),
            signature=CodeFunctionSignature.from_dict(raw.get("signature") or {}),
            dependencies=[str(x) for x in raw.get("dependencies") or []],
            trigger_policy=TriggerPolicy.from_dict(raw.get("trigger_policy")),
            quality_gate=dict(raw.get("quality_gate") or {}),
            test_cases=cases,
            created_at=str(raw.get("created_at") or now_iso()),
            source_run_id=str(raw.get("source_run_id") or ""),
            domain_keywords=[str(x) for x in raw.get("domain_keywords") or []],
        )


@dataclass(slots=True)
class CodePatch:
    reason: str
    original_code: str
    patched_code: str
    diff_summary: str
    llm_reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CodePatch:
        return cls(
            reason=str(raw.get("reason") or ""),
            original_code=str(raw.get("original_code") or ""),
            patched_code=str(raw.get("patched_code") or ""),
            diff_summary=str(raw.get("diff_summary") or ""),
            llm_reasoning=str(raw.get("llm_reasoning") or ""),
        )


@dataclass(slots=True)
class CodeDiagnosis:
    error_type: str
    error_message: str
    traceback_str: str
    failing_line: str
    local_variables: dict[str, Any] = field(default_factory=dict)
    suggested_fix_type: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CodeDiagnosis:
        return cls(
            error_type=str(raw.get("error_type") or ""),
            error_message=str(raw.get("error_message") or ""),
            traceback_str=str(raw.get("traceback_str") or ""),
            failing_line=str(raw.get("failing_line") or ""),
            local_variables=dict(raw.get("local_variables") or {}),
            suggested_fix_type=str(raw.get("suggested_fix_type") or "unknown"),
        )


@dataclass(slots=True)
class CodeValidationResult:
    safe: bool
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CodeValidationResult:
        return cls(
            safe=bool(raw.get("safe", False)),
            issues=[str(x) for x in raw.get("issues") or []],
        )


@dataclass(slots=True)
class CodeSandboxResult:
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""
    traceback_str: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CodeSandboxResult:
        return cls(
            success=bool(raw.get("success", False)),
            output=dict(raw.get("output") or {}),
            error_type=str(raw.get("error_type") or ""),
            error_message=str(raw.get("error_message") or ""),
            traceback_str=str(raw.get("traceback_str") or ""),
            duration_ms=float(raw.get("duration_ms") or 0.0),
        )


@dataclass(slots=True)
class CodeTestResult:
    case_id: str
    passed: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CodeSkillRun:
    run_id: str
    skill_id: str
    stage: str
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    patch: CodePatch | None = None
    error: str = ""
    diagnosis: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=now_iso)
    completed_at: str = ""

    def complete(self, output_data: dict[str, Any], stage: str | None = None) -> CodeSkillRun:
        self.output_data = output_data
        if stage:
            self.stage = stage
        self.completed_at = now_iso()
        return self

    def fail(self, error: str, stage: str | None = None) -> CodeSkillRun:
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
            "input_data": dict(self.input_data),
            "output_data": dict(self.output_data),
            "patch": self.patch.to_dict() if self.patch else None,
            "error": self.error,
            "diagnosis": dict(self.diagnosis),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(slots=True)
class CodeSkill:
    skill_id: str
    name: str
    domain: str
    active_version: int
    versions: list[CodeSkillVersion] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "domain": self.domain,
            "active_version": self.active_version,
            "versions": [v.to_dict() for v in self.versions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CodeSkill:
        vers = [CodeSkillVersion.from_dict(v) for v in raw.get("versions") or [] if isinstance(v, dict)]
        return cls(
            skill_id=str(raw.get("skill_id") or ""),
            name=str(raw.get("name") or ""),
            domain=str(raw.get("domain") or ""),
            active_version=int(raw.get("active_version") or 1),
            versions=vers,
            created_at=str(raw.get("created_at") or now_iso()),
            updated_at=str(raw.get("updated_at") or now_iso()),
        )

    def get_active_version(self) -> CodeSkillVersion:
        for version in self.versions:
            if version.version == self.active_version:
                return version
        if not self.versions:
            raise ValueError(f"CodeSkill {self.skill_id} has no versions")
        return self.versions[-1]

    def add_version(self, version: CodeSkillVersion, *, activate: bool = True) -> None:
        self.versions.append(version)
        if activate:
            self.active_version = version.version
        self.updated_at = now_iso()
