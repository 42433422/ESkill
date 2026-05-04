"""JSON-backed store for code skills."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .models import CodeSkill, CodeSkillRun


class JsonCodeSkillStore:
    """File-backed registry for CodeSkill records."""

    def __init__(self, path: str | Path, lock_timeout: float = 5.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._lock_timeout = lock_timeout
        if not self.path.exists():
            self._write(self._empty_data())

    def list_code_skills(self) -> list[CodeSkill]:
        data = self._read()
        return [CodeSkill.from_dict(raw) for raw in data.get("code_skills", {}).values()]

    def get_code_skill(self, skill_id: str) -> CodeSkill:
        data = self._read()
        raw = data.get("code_skills", {}).get(skill_id)
        if not raw:
            raise KeyError(f"Code skill not found: {skill_id}")
        return CodeSkill.from_dict(raw)

    def has_code_skill(self, skill_id: str) -> bool:
        data = self._read()
        return skill_id in (data.get("code_skills") or {})

    def save_code_skill(self, skill: CodeSkill) -> None:
        with self._lock:
            data = self._read()
            data.setdefault("code_skills", {})[skill.skill_id] = skill.to_dict()
            self._write(data)

    def append_code_run(self, run: CodeSkillRun) -> None:
        with self._lock:
            data = self._read()
            data.setdefault("code_runs", []).append(run.to_dict())
            self._write(data)

    def list_code_runs(self, skill_id: str | None = None) -> list[dict[str, Any]]:
        runs = list(self._read().get("code_runs", []))
        if skill_id is None:
            return runs
        return [run for run in runs if run.get("skill_id") == skill_id]

    def append_code_event(self, record: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            data.setdefault("code_events", []).append(dict(record))
            self._write(data)

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
            "code_skills": {},
            "code_runs": [],
            "code_events": [],
        }
