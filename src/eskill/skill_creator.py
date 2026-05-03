from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import ESkill, SkillVersion, TriggerPolicy
from .store import JsonSkillStore

# Lowercase ids; hyphens OK; must start/end with alphanumeric (length 1: any alnum).
_SKILL_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


def normalize_skill_id(raw: str) -> str:
    """Turn arbitrary text into a safe skill_id: lowercase, hyphen-separated."""
    s = raw.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        raise ValueError("skill_id cannot be empty after normalization")
    if len(s) > 64:
        s = s[:64].rstrip("-")
    if not _SKILL_ID_PATTERN.match(s):
        s = f"x{s}" if s else "skill"
        s = re.sub(r"[^a-z0-9_-]", "", s)
        if not _SKILL_ID_PATTERN.match(s):
            s = "skill"
    return s


def validate_static_logic(logic: dict[str, Any]) -> None:
    """
    Ensure static_logic can be executed by ESkillRuntime._execute_static.
    Raises ValueError with a human-readable message on failure.
    """
    if not isinstance(logic, dict):
        raise ValueError("static_logic must be a dict")

    logic_type = str(logic.get("type") or "template_transform")

    if logic_type == "template_transform":
        return

    if logic_type == "employee_task":
        task = logic.get("task_template") or logic.get("task")
        if not task:
            raise ValueError("employee_task requires task_template or task")
        return

    if logic_type == "pipeline":
        steps = logic.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("pipeline requires a non-empty steps list")
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"pipeline step #{idx} must be an object")
            step_type = str(step.get("type") or "template_transform")
            if step_type == "template_transform" and not str(step.get("template") or ""):
                raise ValueError(f"pipeline step #{idx} (template_transform) needs template")
            if step_type == "set_value" and "value" not in step:
                raise ValueError(f"pipeline step #{idx} (set_value) needs value")
            if step_type == "tool_call":
                tool = str(step.get("tool") or "")
                if tool not in {"echo", "extract_keys"}:
                    raise ValueError(
                        f"pipeline step #{idx}: tool {tool!r} not in allowlist "
                        "(echo, extract_keys)"
                    )
        return

    raise ValueError(
        f"Unsupported static logic type: {logic_type}. "
        "Use template_transform, employee_task, or pipeline."
    )


@dataclass(slots=True)
class SkillBlueprint:
    """
    Declarative definition of a new ESkill (version 1).
    Suitable for APIs, LLM output, or hand-authored configs.
    """

    skill_id: str
    name: str
    domain: str
    static_logic: dict[str, Any]
    trigger_policy: TriggerPolicy = field(default_factory=TriggerPolicy)
    quality_gate: dict[str, Any] = field(default_factory=dict)
    dynamic_template: str | None = None
    allow_dynamic_steps: bool | None = None

    def __post_init__(self) -> None:
        self.skill_id = self.skill_id.strip()
        self.name = self.name.strip()
        self.domain = self.domain.strip()
        if not self.skill_id:
            raise ValueError("skill_id is required")
        if not self.name:
            raise ValueError("name is required")
        if len(self.skill_id) > 64:
            raise ValueError("skill_id must be at most 64 characters")
        if not _SKILL_ID_PATTERN.match(self.skill_id):
            raise ValueError(
                f"skill_id {self.skill_id!r} must be lowercase [a-z0-9-], "
                "start and end with a letter or digit"
            )

        logic = dict(self.static_logic)
        logic_type = str(logic.get("type") or "template_transform")
        if self.dynamic_template and logic_type == "template_transform":
            logic.setdefault("dynamic_template", self.dynamic_template)
        if self.allow_dynamic_steps is True:
            logic.setdefault("allow_steps", True)
        self.static_logic = logic

        validate_static_logic(self.static_logic)

    def to_manifest(self) -> dict[str, Any]:
        """JSON-serializable summary for UIs or agent discovery."""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "domain": self.domain,
            "logic_type": self.static_logic.get("type") or "template_transform",
            "required_fields": list(self.static_logic.get("required_fields") or []),
            "output_var": self.static_logic.get("output_var"),
            "domain_keywords": self.static_logic.get("domain_keywords"),
            "quality_gate": dict(self.quality_gate),
            "trigger_policy": self.trigger_policy.to_dict(),
        }

    def to_agent_brief_markdown(self) -> str:
        """Short markdown an IDE agent can inject as context for when to use this skill."""
        manifest = self.to_manifest()
        lines = [
            f"### ESkill: {manifest['name']} (`{manifest['skill_id']}`)",
            "",
            f"- **Domain**: {self.domain}",
            f"- **Logic**: `{manifest['logic_type']}`",
        ]
        rf = manifest.get("required_fields") or []
        if rf:
            lines.append(f"- **Required input keys**: {', '.join(rf)}")
        qk = self.static_logic.get("domain_keywords")
        if qk:
            kw = qk if isinstance(qk, list) else [qk]
            lines.append(f"- **Domain keywords**: {', '.join(str(x) for x in kw)}")
        lines.append("")
        lines.append("Register via `SkillCreator.create(...)` and run with `ESkillRuntime.run`.")
        return "\n".join(lines)

    def build_eskill(self) -> ESkill:
        version = SkillVersion(
            version=1,
            static_logic=dict(self.static_logic),
            trigger_policy=self.trigger_policy,
            quality_gate=dict(self.quality_gate),
        )
        return ESkill(
            skill_id=self.skill_id,
            name=self.name,
            domain=self.domain,
            active_version=1,
            versions=[version],
        )

    @staticmethod
    def template_transform(
        *,
        skill_id: str,
        name: str,
        domain: str,
        template: str,
        required_fields: list[str],
        output_var: str = "result",
        domain_keywords: list[str] | None = None,
        dynamic_template: str | None = None,
        quality_gate: dict[str, Any] | None = None,
        trigger_policy: TriggerPolicy | None = None,
    ) -> SkillBlueprint:
        logic: dict[str, Any] = {
            "type": "template_transform",
            "template": template,
            "required_fields": required_fields,
            "output_var": output_var,
        }
        if domain_keywords:
            logic["domain_keywords"] = domain_keywords
        return SkillBlueprint(
            skill_id=skill_id,
            name=name,
            domain=domain,
            static_logic=logic,
            trigger_policy=trigger_policy or TriggerPolicy(),
            quality_gate=quality_gate or {},
            dynamic_template=dynamic_template,
            allow_dynamic_steps=bool(dynamic_template),
        )


class SkillCreator:
    """Registers new skills into JsonSkillStore with validation and optional overwrite."""

    @staticmethod
    def create(
        store: JsonSkillStore,
        blueprint: SkillBlueprint,
        *,
        overwrite: bool = False,
    ) -> ESkill:
        if store.has_skill(blueprint.skill_id) and not overwrite:
            raise KeyError(
                f"Skill already exists: {blueprint.skill_id}. "
                "Pass overwrite=True to replace."
            )
        skill = blueprint.build_eskill()
        store.save_skill(skill)
        return skill

    @staticmethod
    def ensure(
        store: JsonSkillStore,
        blueprint: SkillBlueprint,
    ) -> tuple[ESkill, bool]:
        """
        Create if missing; return (skill, created).
        If present, returns stored skill and created=False without merging.
        """
        if store.has_skill(blueprint.skill_id):
            return store.get_skill(blueprint.skill_id), False
        skill = blueprint.build_eskill()
        store.save_skill(skill)
        return skill, True
