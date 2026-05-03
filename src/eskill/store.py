from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from .models import ESkill, EvolutionEvent, SkillHealthReport, SkillRun


class JsonSkillStore:
    """Small file-backed registry with concurrency safety."""

    def __init__(self, path: str | Path, lock_timeout: float = 5.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._lock_timeout = lock_timeout
        if not self.path.exists():
            self._write(self._empty_data())

    def list_skills(self) -> list[ESkill]:
        data = self._read()
        return [ESkill.from_dict(raw) for raw in data.get("skills", {}).values()]

    def get_skill(self, skill_id: str) -> ESkill:
        data = self._read()
        raw = data.get("skills", {}).get(skill_id)
        if not raw:
            raise KeyError(f"Skill not found: {skill_id}")
        return ESkill.from_dict(raw)

    def has_skill(self, skill_id: str) -> bool:
        data = self._read()
        return skill_id in (data.get("skills") or {})

    def save_skill(self, skill: ESkill) -> None:
        with self._lock:
            data = self._read()
            data.setdefault("skills", {})[skill.skill_id] = skill.to_dict()
            self._write(data)

    def append_run(self, run: SkillRun) -> None:
        with self._lock:
            data = self._read()
            data.setdefault("runs", []).append(run.to_dict())
            self._write(data)

    def list_runs(self, skill_id: str | None = None) -> list[dict[str, Any]]:
        runs = list(self._read().get("runs", []))
        if skill_id is None:
            return runs
        return [run for run in runs if run.get("skill_id") == skill_id]

    def append_event(self, event: EvolutionEvent) -> None:
        with self._lock:
            data = self._read()
            data.setdefault("events", []).append(event.to_dict())
            self._write(data)

    def list_events(
        self, skill_id: str | None = None, event_type: str | None = None
    ) -> list[dict[str, Any]]:
        events = list(self._read().get("events", []))
        if skill_id is not None:
            events = [event for event in events if event.get("skill_id") == skill_id]
        if event_type is not None:
            events = [event for event in events if event.get("event_type") == event_type]
        return events

    def append_health_report(self, report: SkillHealthReport) -> None:
        self.append_record("health_reports", report.to_dict())

    def list_health_reports(self, skill_id: str | None = None) -> list[dict[str, Any]]:
        reports = self.list_records("health_reports")
        if skill_id is None:
            return reports
        return [report for report in reports if report.get("skill_id") == skill_id]

    def append_record(self, collection: str, record: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            data.setdefault(collection, []).append(dict(record))
            self._write(data)

    def list_records(self, collection: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self._read().get(collection, []) if isinstance(row, dict)]

    def get_version_diff(self, skill_id: str, v1: int, v2: int | None = None) -> dict[str, Any]:
        skill = self.get_skill(skill_id)
        versions = sorted(skill.versions, key=lambda v: v.version)
        version_map = {v.version: v for v in versions}
        if v1 not in version_map:
            raise ValueError(f"Version {v1} not found for skill {skill_id}")
        if v2 is None:
            if len(versions) < 2:
                return {"added": {}, "removed": {}, "changed": {}, "message": "No previous version"}
            v2 = max(version_map.keys())
        if v2 not in version_map:
            raise ValueError(f"Version {v2} not found for skill {skill_id}")
        logic1 = version_map[v1].static_logic
        logic2 = version_map[v2].static_logic
        added = {k: v for k, v in logic2.items() if k not in logic1}
        removed = {k: v for k, v in logic1.items() if k not in logic2}
        changed = {k: {"old": logic1[k], "new": logic2[k]} for k in logic1 if k in logic2 and logic1[k] != logic2[k]}
        return {"added": added, "removed": removed, "changed": changed, "from_version": v1, "to_version": v2}

    def set_active_version(self, skill_id: str, version: int) -> ESkill:
        with self._lock:
            skill = self.get_skill(skill_id)
            if not any(v.version == version for v in skill.versions):
                raise ValueError(f"Skill {skill_id} does not have version {version}")
            skill.active_version = version
            self.save_skill(skill)
            return skill

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = self._empty_data()
        for key, value in self._empty_data().items():
            data.setdefault(key, value.copy() if isinstance(value, dict) else list(value))
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _empty_data(self) -> dict[str, Any]:
        return {
            "skills": {},
            "runs": [],
            "events": [],
            "health_reports": [],
            "policy_states": [],
            "crystals": [],
            "memories": [],
            "test_suites": [],
            "packages": [],
        }
