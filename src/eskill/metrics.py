from __future__ import annotations

from typing import Any


class SkillMetricsCollector:
    """Collects runtime metrics for a single skill."""

    def __init__(self, skill_id: str):
        self.skill_id = skill_id
        self._total_runs = 0
        self._static_runs = 0
        self._dynamic_runs = 0
        self._solidified_runs = 0
        self._failed_runs = 0
        self._rollback_count = 0
        self._version_history: list[dict[str, Any]] = []
        self._quality_scores: list[float] = []
        self._last_error: str | None = None
        self._avg_quality_score: float = 0.0

    def record_run(self, stage: str, error: str = "", quality_score: float | None = None) -> None:
        self._total_runs += 1
        if stage == "static":
            self._static_runs += 1
        elif stage == "dynamic":
            self._dynamic_runs += 1
        elif stage == "solidified":
            self._solidified_runs += 1
        if error:
            self._failed_runs += 1
            self._last_error = error
        if quality_score is not None:
            self._quality_scores.append(quality_score)
            self._avg_quality_score = sum(self._quality_scores) / len(self._quality_scores)

    def record_version(self, version: int, source_run_id: str, reason: str = "") -> None:
        self._version_history.append({
            "version": version,
            "source_run_id": source_run_id,
            "reason": reason,
        })

    def record_rollback(self) -> None:
        self._rollback_count += 1

    def get_summary(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "total_runs": self._total_runs,
            "static_runs": self._static_runs,
            "dynamic_runs": self._dynamic_runs,
            "solidified_runs": self._solidified_runs,
            "failed_runs": self._failed_runs,
            "rollback_count": self._rollback_count,
            "success_rate": round(
                (self._total_runs - self._failed_runs) / max(self._total_runs, 1), 4
            ),
            "avg_quality_score": round(self._avg_quality_score, 4),
            "version_history": self._version_history,
            "last_error": self._last_error,
        }


class RuntimeMetrics:
    """Aggregates metrics across all skills in the runtime."""

    def __init__(self):
        self._collectors: dict[str, SkillMetricsCollector] = {}

    def get_or_create(self, skill_id: str) -> SkillMetricsCollector:
        if skill_id not in self._collectors:
            self._collectors[skill_id] = SkillMetricsCollector(skill_id)
        return self._collectors[skill_id]

    def get_summary(self) -> dict[str, Any]:
        total_runs = sum(c._total_runs for c in self._collectors.values())
        total_failed = sum(c._failed_runs for c in self._collectors.values())
        total_solidified = sum(c._solidified_runs for c in self._collectors.values())

        return {
            "total_skills": len(self._collectors),
            "total_runs": total_runs,
            "total_failed": total_failed,
            "total_solidified": total_solidified,
            "overall_success_rate": round(
                (total_runs - total_failed) / max(total_runs, 1), 4
            ),
            "skills": {
                skill_id: collector.get_summary()
                for skill_id, collector in self._collectors.items()
            },
        }

    def get_skill_report(self, skill_id: str) -> dict[str, Any] | None:
        collector = self._collectors.get(skill_id)
        return collector.get_summary() if collector else None
