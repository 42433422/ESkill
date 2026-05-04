"""Hybrid runtime: config-layer first, optional code-layer fallback."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..models import DynamicPatch, SkillRun, TriggerPolicy
from ..runtime import ESkillRuntime
from .models import CodeSkillRun
from .runtime import CodeSkillRuntime


class HybridSkillRuntime:
    """Dispatch by `ESkill.skill_type`: config | code | hybrid."""

    def __init__(
        self,
        config_runtime: ESkillRuntime,
        code_runtime: CodeSkillRuntime,
        *,
        config_store: Any | None = None,
    ):
        self.config_runtime = config_runtime
        self.code_runtime = code_runtime
        self.config_store = config_store or config_runtime.store

    def run(
        self,
        skill_id: str,
        input_data: dict[str, Any],
        *,
        trigger_policy: TriggerPolicy | None = None,
        quality_gate: dict[str, Any] | None = None,
        force_dynamic: bool = False,
        solidify: bool = True,
    ) -> SkillRun:
        skill = self.config_store.get_skill(skill_id)
        st = getattr(skill, "skill_type", None) or "config"

        if st == "config":
            return self.config_runtime.run(
                skill_id,
                input_data,
                trigger_policy=trigger_policy,
                quality_gate=quality_gate,
                force_dynamic=force_dynamic,
                solidify=solidify,
            )

        if st == "code":
            cr = self.code_runtime.run(
                skill_id,
                input_data,
                force_dynamic=force_dynamic,
                solidify=solidify,
                trigger_policy=trigger_policy,
                quality_gate=quality_gate,
            )
            return self._code_to_skill_run(cr)

        # hybrid
        r1 = self.config_runtime.run(
            skill_id,
            input_data,
            trigger_policy=trigger_policy,
            quality_gate=quality_gate,
            force_dynamic=force_dynamic,
            solidify=solidify,
        )
        if r1.stage in ("static", "solidified"):
            return r1

        cr = self.code_runtime.run(
            skill_id,
            input_data,
            solidify=solidify,
            trigger_policy=trigger_policy,
            quality_gate=quality_gate,
        )
        return self._code_to_skill_run(cr)

    def _code_to_skill_run(self, cr: CodeSkillRun) -> SkillRun:
        patch = None
        if cr.patch:
            patch = DynamicPatch(
                reason=cr.patch.reason,
                changes={
                    "patched_code": cr.patch.patched_code,
                    "original_code": cr.patch.original_code,
                    "diff_summary": cr.patch.diff_summary,
                    "llm_reasoning": cr.patch.llm_reasoning,
                },
            )
        return SkillRun(
            run_id=cr.run_id,
            skill_id=cr.skill_id,
            stage=cr.stage,
            input_data=deepcopy(cr.input_data),
            output_data=dict(cr.output_data),
            patch=patch,
            error=cr.error,
            diagnosis=dict(cr.diagnosis),
        )
