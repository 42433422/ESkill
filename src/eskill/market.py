from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .models import ESkill, now_iso
from .store import JsonSkillStore
from .testing import SkillTestRunner, SkillTestSuite


@dataclass(slots=True)
class SkillPackage:
    skill: ESkill
    test_suite: SkillTestSuite
    audit_summary: dict[str, Any] = field(default_factory=dict)
    rating: float = 0.0
    downloads: int = 0
    signature: str = ""
    exported_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SkillPackage":
        return cls(
            skill=ESkill.from_dict(dict(raw.get("skill") or {})),
            test_suite=SkillTestSuite.from_dict(dict(raw.get("test_suite") or {})),
            audit_summary=dict(raw.get("audit_summary") or {}),
            rating=float(raw.get("rating") or 0.0),
            downloads=int(raw.get("downloads") or 0),
            signature=str(raw.get("signature") or ""),
            exported_at=str(raw.get("exported_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["skill"] = self.skill.to_dict()
        data["test_suite"] = self.test_suite.to_dict()
        return data


class SkillPackageManager:
    def __init__(self, store: JsonSkillStore):
        self.store = store

    def export(self, skill_id: str, test_suite: SkillTestSuite) -> SkillPackage:
        package = SkillPackage(
            skill=self.store.get_skill(skill_id),
            test_suite=test_suite,
            audit_summary={
                "events": len(self.store.list_events(skill_id=skill_id)),
                "runs": len(self.store.list_runs(skill_id=skill_id)),
            },
        )
        self.store.append_record("packages", package.to_dict())
        return package

    def install(self, package: SkillPackage, *, overwrite: bool = False) -> ESkill:
        if self.store.has_skill(package.skill.skill_id) and not overwrite:
            raise KeyError(f"Skill already exists: {package.skill.skill_id}")
        self.store.save_skill(package.skill)
        self.store.append_record("test_suites", package.test_suite.to_dict())
        return package.skill


class ValidationCenter:
    def __init__(self, store: JsonSkillStore):
        self.store = store

    def validate_package(self, package: SkillPackage) -> bool:
        original: ESkill | None = None
        if self.store.has_skill(package.skill.skill_id):
            original = self.store.get_skill(package.skill.skill_id)
        self.store.save_skill(package.skill)
        result = SkillTestRunner.from_store(self.store).run_suite(package.test_suite)
        if original:
            self.store.save_skill(original)
        return result.passed
