from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .models import SkillRun, now_iso
from .runtime import ESkillRuntime


@dataclass(slots=True)
class SkillTestCase:
    case_id: str
    input_data: dict[str, Any]
    expected_keys: list[str] = field(default_factory=list)
    contains_all: list[str] = field(default_factory=list)
    contains_any: list[str] = field(default_factory=list)
    expected_stage: str | None = None
    min_score: float = 0.0
    quality_gate: dict[str, Any] = field(default_factory=dict)
    allow_solidify: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SkillTestCase":
        return cls(
            case_id=str(raw.get("case_id") or raw.get("id") or ""),
            input_data=dict(raw.get("input_data") or {}),
            expected_keys=[str(x) for x in raw.get("expected_keys") or []],
            contains_all=[str(x) for x in raw.get("contains_all") or []],
            contains_any=[str(x) for x in raw.get("contains_any") or []],
            expected_stage=str(raw["expected_stage"]) if raw.get("expected_stage") else None,
            min_score=float(raw.get("min_score") or 0.0),
            quality_gate=dict(raw.get("quality_gate") or {}),
            allow_solidify=bool(raw.get("allow_solidify", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SkillTestSuite:
    suite_id: str
    skill_id: str
    cases: list[SkillTestCase]
    description: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SkillTestSuite":
        return cls(
            suite_id=str(raw.get("suite_id") or raw.get("id") or ""),
            skill_id=str(raw.get("skill_id") or ""),
            description=str(raw.get("description") or ""),
            cases=[
                SkillTestCase.from_dict(case)
                for case in raw.get("cases") or []
                if isinstance(case, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "skill_id": self.skill_id,
            "description": self.description,
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(slots=True)
class SkillTestResult:
    case_id: str
    passed: bool
    stage: str
    score: float
    issues: list[str] = field(default_factory=list)
    output_data: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SkillSuiteResult:
    suite_id: str
    skill_id: str
    passed: bool
    results: list[SkillTestResult]
    created_at: str = field(default_factory=now_iso)

    @property
    def failed_cases(self) -> list[str]:
        return [result.case_id for result in self.results if not result.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "skill_id": self.skill_id,
            "passed": self.passed,
            "results": [result.to_dict() for result in self.results],
            "created_at": self.created_at,
        }


class SkillTestRunner:
    def __init__(self, runtime: ESkillRuntime):
        self.runtime = runtime

    @classmethod
    def from_store(cls, store: Any) -> "SkillTestRunner":
        return cls(ESkillRuntime(store))

    def run_suite(self, suite: SkillTestSuite) -> SkillSuiteResult:
        results = [self.run_case(suite.skill_id, case) for case in suite.cases]
        return SkillSuiteResult(
            suite_id=suite.suite_id,
            skill_id=suite.skill_id,
            passed=all(result.passed for result in results),
            results=results,
        )

    def run_case(self, skill_id: str, case: SkillTestCase) -> SkillTestResult:
        run = self.runtime.run(
            skill_id,
            case.input_data,
            quality_gate=case.quality_gate or None,
            solidify=case.allow_solidify,
        )
        return self._assert_case(case, run)

    def _assert_case(self, case: SkillTestCase, run: SkillRun) -> SkillTestResult:
        issues: list[str] = []
        text = " ".join(str(v) for v in run.output_data.values())
        for key in case.expected_keys:
            if key not in run.output_data:
                issues.append(f"missing_key:{key}")
        for token in case.contains_all:
            if token not in text:
                issues.append(f"missing_text:{token}")
        if case.contains_any and not any(token in text for token in case.contains_any):
            issues.append("missing_any_text")
        if case.expected_stage and run.stage != case.expected_stage:
            issues.append(f"stage:{run.stage}!={case.expected_stage}")
        if run.error:
            issues.append(f"run_error:{run.error}")

        score = 1.0 if not issues else max(0.0, 1.0 - 0.2 * len(issues))
        if score < case.min_score:
            issues.append(f"score:{score}<{case.min_score}")

        return SkillTestResult(
            case_id=case.case_id,
            passed=not issues,
            stage=run.stage,
            score=round(score, 4),
            issues=issues,
            output_data=dict(run.output_data),
            run_id=run.run_id,
        )
