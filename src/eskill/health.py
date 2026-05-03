from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .models import SkillHealthReport
from .runtime import ESkillRuntime
from .store import JsonSkillStore
from .testing import SkillSuiteResult, SkillTestRunner, SkillTestSuite


@dataclass(slots=True)
class DependencySignal:
    name: str
    kind: str
    detail: str = ""
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillHealthChecker:
    def __init__(self, store: JsonSkillStore, runtime: ESkillRuntime | None = None):
        self.store = store
        self.runtime = runtime or ESkillRuntime(store)

    def run_suite(
        self,
        suite: SkillTestSuite,
        *,
        auto_downgrade: bool = True,
        dependency_signals: list[DependencySignal] | None = None,
    ) -> SkillHealthReport:
        skill = self.store.get_skill(suite.skill_id)
        original_version = skill.active_version
        result = SkillTestRunner(self.runtime).run_suite(suite)
        downgraded_to = None

        if not result.passed and auto_downgrade:
            downgraded_to = self._downgrade_to_healthy_version(suite, original_version)

        report = SkillHealthReport(
            skill_id=suite.skill_id,
            suite_id=suite.suite_id,
            passed=result.passed,
            active_version=original_version,
            checked_cases=len(result.results),
            failed_cases=result.failed_cases,
            downgraded_to_version=downgraded_to,
            dependency_signals=[signal.to_dict() for signal in dependency_signals or []],
        )
        self.store.append_health_report(report)
        return report

    def evaluate(self, suite: SkillTestSuite) -> SkillSuiteResult:
        return SkillTestRunner(self.runtime).run_suite(suite)

    def _downgrade_to_healthy_version(
        self, suite: SkillTestSuite, current_version: int
    ) -> int | None:
        skill = self.store.get_skill(suite.skill_id)
        candidates = sorted(
            (version.version for version in skill.versions if version.version < current_version),
            reverse=True,
        )
        for version in candidates:
            self.store.set_active_version(suite.skill_id, version)
            if SkillTestRunner(self.runtime).run_suite(suite).passed:
                return version
        self.store.set_active_version(suite.skill_id, current_version)
        return None
