from __future__ import annotations

from typing import Any

from .adapter import DictSkillAdapter, SkillAdapter
from .llm_adapter import LLMPatchGenerator
from .models import DynamicPatch, ESkill, SkillVersion, TriggerPolicy
from .runtime import ESkillRuntime, RuleBasedDynamicAdapter
from .store import JsonSkillStore


class ESkillWrapper:
    def __init__(
        self,
        skill: SkillAdapter,
        store: JsonSkillStore,
        llm_generator: LLMPatchGenerator | None = None,
        quality_gate: dict[str, Any] | None = None,
        trigger_policy: dict[str, bool] | None = None,
    ):
        self.skill = skill
        self.store = store
        self.llm_generator = llm_generator
        self.quality_gate = quality_gate or {}
        self.trigger_policy = trigger_policy or {}
        self._runtime = self._build_runtime()

    def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        run = self._runtime.run(
            self.skill.skill_id,
            input_data,
            quality_gate=self.quality_gate or None,
        )
        if run.output_data and "raw_result" in run.output_data:
            raw = run.output_data.pop("raw_result")
            if isinstance(raw, dict):
                return {**run.output_data, **raw}
            return {**run.output_data, "result": raw}
        return run.output_data

    def describe(self) -> dict[str, Any]:
        skill_desc = self.skill.describe()
        return {
            **skill_desc,
            "self_healing": True,
            "llm_enabled": self.llm_generator is not None,
            "quality_gate": self.quality_gate,
        }

    def get_run_history(self, limit: int = 10) -> list[dict[str, Any]]:
        runs = self.store.list_runs(self.skill.skill_id)
        return runs[-limit:]

    def _build_runtime(self) -> ESkillRuntime:
        adapter = (
            _LLMBackedAdapter(self.llm_generator) if self.llm_generator else RuleBasedDynamicAdapter()
        )

        runtime = _WrappedRuntime(
            self.skill,
            self.store,
            adapter,
            self.quality_gate,
            self.trigger_policy,
        )
        return runtime


class _WrappedRuntime(ESkillRuntime):
    def __init__(
        self,
        skill: SkillAdapter,
        store: JsonSkillStore,
        adapter: RuleBasedDynamicAdapter | None = None,
        quality_gate: dict[str, Any] | None = None,
        trigger_policy: dict[str, bool] | None = None,
    ):
        super().__init__(store, adapter)
        self.skill = skill
        self._quality_gate = quality_gate or {}
        self._trigger_policy = trigger_policy or {}
        self._init_skill_record()

    def _init_skill_record(self) -> None:
        skill_desc = self.skill.describe()
        skill_id = skill_desc["skill_id"]
        try:
            self.store.get_skill(skill_id)
            return
        except KeyError:
            pass

        if isinstance(self.skill, DictSkillAdapter):
            logic = {
                "type": "template_transform",
                "template": self.skill.template,
                "required_fields": self.skill.required_fields,
                "output_var": self.skill.output_var,
                "domain_keywords": skill_desc.get("domain_keywords", []),
            }
        else:
            logic = {
                "type": "function_skill",
                "skill_ref": skill_id,
                "domain_keywords": skill_desc.get("domain_keywords", []),
                "required_fields": skill_desc.get("required_fields", []),
            }

        policy = TriggerPolicy(
            on_error=self._trigger_policy.get("on_error", True),
            on_quality_below_threshold=self._trigger_policy.get("on_quality_below_threshold", True),
            force_dynamic=self._trigger_policy.get("force_dynamic", False),
        )

        eskill = ESkill(
            skill_id=skill_id,
            name=skill_desc["name"],
            domain=skill_desc["domain"],
            active_version=1,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic=logic,
                    trigger_policy=policy,
                    quality_gate=self._quality_gate,
                )
            ],
        )
        self.store.save_skill(eskill)

    def _execute_static(self, skill_id: str, logic: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
        logic_type = logic.get("type")
        if logic_type == "function_skill":
            result = self.skill.execute(input_data)
            return {"raw_result": result}
        return super()._execute_static(skill_id, logic, input_data)


class _LLMBackedAdapter(RuleBasedDynamicAdapter):
    def __init__(self, llm_generator: LLMPatchGenerator):
        self.llm_generator = llm_generator

    def propose(
        self,
        *,
        reason: str,
        logic: dict[str, Any],
        input_data: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
        quality_report: dict[str, Any] | None = None,
    ) -> DynamicPatch:
        if self.llm_generator:
            try:
                return self.llm_generator.generate_patch(
                    reason=reason,
                    logic=logic,
                    input_data=input_data,
                    history=history,
                    error=error,
                    quality_report=quality_report,
                )
            except Exception:
                pass
        return super().propose(
            reason=reason,
            logic=logic,
            input_data=input_data,
            history=history,
            error=error,
        )
