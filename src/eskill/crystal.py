from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from .models import now_iso
from .store import JsonSkillStore


@dataclass(slots=True)
class SkillCrystal:
    skill_id: str
    problem_pattern: str
    repair_strategy: dict[str, Any]
    applicability: dict[str, Any] = field(default_factory=dict)
    validation_method: dict[str, Any] = field(default_factory=dict)
    source_event_id: str = ""
    crystal_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SkillCrystal":
        return cls(
            skill_id=str(raw.get("skill_id") or ""),
            problem_pattern=str(raw.get("problem_pattern") or ""),
            repair_strategy=dict(raw.get("repair_strategy") or {}),
            applicability=dict(raw.get("applicability") or {}),
            validation_method=dict(raw.get("validation_method") or {}),
            source_event_id=str(raw.get("source_event_id") or ""),
            crystal_id=str(raw.get("crystal_id") or uuid4().hex),
            created_at=str(raw.get("created_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CrystalLibrary:
    def __init__(self, store: JsonSkillStore):
        self.store = store

    def add(self, crystal: SkillCrystal) -> SkillCrystal:
        self.store.append_record("crystals", crystal.to_dict())
        return crystal

    def list(self, skill_id: str | None = None) -> list[SkillCrystal]:
        rows = self.store.list_records("crystals")
        if skill_id is not None:
            rows = [row for row in rows if row.get("skill_id") == skill_id]
        return [SkillCrystal.from_dict(row) for row in rows]

    def search(self, skill_id: str, input_data: dict[str, Any], limit: int = 5) -> list[SkillCrystal]:
        text = json.dumps(input_data, ensure_ascii=False).lower()
        matches: list[SkillCrystal] = []
        for crystal in reversed(self.list(skill_id)):
            keywords = crystal.applicability.get("keywords") or []
            pattern = crystal.problem_pattern.lower()
            if pattern and pattern in text:
                matches.append(crystal)
            elif any(str(keyword).lower() in text for keyword in keywords):
                matches.append(crystal)
            if len(matches) >= limit:
                break
        return matches


class SkillCrystalizer:
    def __init__(self, store: JsonSkillStore):
        self.store = store
        self.library = CrystalLibrary(store)

    def crystalize_from_event(self, event: dict[str, Any]) -> SkillCrystal | None:
        if event.get("event_type") not in {"version_solidified", "dynamic_completed"}:
            return None
        patch = event.get("patch")
        if not isinstance(patch, dict):
            return None
        validation = event.get("validation") if isinstance(event.get("validation"), dict) else {}
        crystal = SkillCrystal(
            skill_id=str(event.get("skill_id") or ""),
            problem_pattern=str(event.get("trigger_signal") or "dynamic_adjustment"),
            repair_strategy=dict(patch.get("changes") or patch),
            applicability={
                "trigger_signal": event.get("trigger_signal"),
                "strategy": event.get("strategy"),
                "keywords": self._keywords_from_patch(patch),
            },
            validation_method=validation,
            source_event_id=str(event.get("event_id") or ""),
        )
        return self.library.add(crystal)

    def crystalize_successes(self, skill_id: str) -> list[SkillCrystal]:
        crystals: list[SkillCrystal] = []
        for event in self.store.list_events(skill_id=skill_id):
            crystal = self.crystalize_from_event(event)
            if crystal:
                crystals.append(crystal)
        return crystals

    def _keywords_from_patch(self, patch: dict[str, Any]) -> list[str]:
        text = json.dumps(patch, ensure_ascii=False).lower()
        tokens = [token.strip("${}:,. ") for token in text.replace('"', " ").split()]
        return sorted({token for token in tokens if len(token) >= 4})[:12]
