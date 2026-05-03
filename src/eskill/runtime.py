from __future__ import annotations

import json
import re
from copy import deepcopy
from string import Template
from typing import Any
from uuid import uuid4

from .metrics import RuntimeMetrics, SkillMetricsCollector
from .models import DynamicPatch, ESkill, EvolutionEvent, SkillRun, SkillVersion, TriggerPolicy
from .store import JsonSkillStore


class RuleBasedDynamicAdapter:
    """Strategy-engine stand-in for an LLM patch generator."""

    def propose(
        self,
        *,
        reason: str,
        logic: dict[str, Any],
        input_data: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> DynamicPatch:
        history = history or []
        changes: dict[str, Any] = {
            "metadata": {"adapted_for": reason, "history_matches": len(history)}
        }
        logic_type = logic.get("type")
        for prior in history:
            prior_changes = prior.get("changes")
            if isinstance(prior_changes, dict):
                changes.update({k: v for k, v in prior_changes.items() if k != "metadata"})
                changes["metadata"]["reused_patch"] = True
                return DynamicPatch(reason=reason, changes=changes)
        if logic_type == "template_transform":
            fallback_template = logic.get("dynamic_template") or logic.get("template")
            if not fallback_template:
                fallback_template = "Dynamic result for ${topic}: ${details}"
            changes["template"] = fallback_template
            changes["required_fields"] = []
            if logic.get("allow_steps"):
                changes["type"] = "pipeline"
                changes["steps"] = [
                    {
                        "id": "render_dynamic_template",
                        "type": "template_transform",
                        "template": fallback_template,
                        "output_var": str(logic.get("output_var") or "result"),
                    },
                    {
                        "id": "attach_adaptation_reason",
                        "type": "set_value",
                        "output_var": "adaptation_reason",
                        "value": reason,
                    },
                ]
        elif logic_type == "employee_task":
            task = str(logic.get("task_template") or logic.get("task") or "")
            changes["task_template"] = f"{task}\nHandle special constraints: ${details}".strip()
            changes["retry_count"] = max(int(logic.get("retry_count") or 0), 1)
        else:
            changes["type"] = "template_transform"
            changes["template"] = "Dynamic result: ${details}"
            changes["required_fields"] = []
        if error:
            changes["metadata"]["last_error"] = str(error)
        if input_data.get("details") and "details" not in changes.get("required_fields", []):
            changes.setdefault("metadata", {})["used_details"] = True
        return DynamicPatch(reason=reason, changes=changes)


class ESkillRuntime:
    def __init__(self, store: JsonSkillStore, adapter: RuleBasedDynamicAdapter | None = None):
        self.store = store
        self.adapter = adapter or RuleBasedDynamicAdapter()
        self.metrics = RuntimeMetrics()

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
        skill = self.store.get_skill(skill_id)
        version = skill.get_active_version()
        policy = trigger_policy or version.trigger_policy
        gate = quality_gate or version.quality_gate
        run = SkillRun(
            run_id=uuid4().hex,
            skill_id=skill.skill_id,
            stage="static",
            input_data=deepcopy(input_data),
        )
        self._record_event(
            skill_id=skill.skill_id,
            run_id=run.run_id,
            event_type="run_started",
            stage="static",
            details={"active_version": version.version},
        )

        try:
            output = self._execute_static(version.static_logic, input_data)
            quality = self._quality_report(output, gate)
            quality_ok = bool(quality["passed"])
            if quality_ok and not policy.force_dynamic and not force_dynamic:
                run.complete(output, "static")
                self.store.append_run(run)
                self.metrics.get_or_create(skill_id).record_run("static", quality_score=quality["score"])
                self._record_event(
                    skill_id=skill.skill_id,
                    run_id=run.run_id,
                    event_type="static_completed",
                    stage=run.stage,
                    validation=quality,
                    details={"active_version": version.version},
                )
                return run
            if not (policy.on_quality_below_threshold or policy.force_dynamic or force_dynamic):
                run.complete(output, "static_quality_failed")
                self.store.append_run(run)
                self.metrics.get_or_create(skill_id).record_run("static_quality_failed")
                self._record_event(
                    skill_id=skill.skill_id,
                    run_id=run.run_id,
                    event_type="validation_failed",
                    stage=run.stage,
                    trigger_signal="quality_gate",
                    validation=quality,
                    details={"active_version": version.version, "dynamic_disabled": True},
                )
                return run
            reason = "force_dynamic" if (policy.force_dynamic or force_dynamic) else "quality_gate"
            return self._run_dynamic(skill, version, input_data, run, reason, None, gate, solidify)
        except Exception as exc:  # noqa: BLE001
            if not policy.on_error:
                run.fail(str(exc), "static_error")
                self.store.append_run(run)
                self.metrics.get_or_create(skill_id).record_run("static_error", str(exc))
                self._record_event(
                    skill_id=skill.skill_id,
                    run_id=run.run_id,
                    event_type="static_error",
                    stage=run.stage,
                    trigger_signal="error",
                    details={"error": str(exc), "dynamic_disabled": True},
                )
                return run
            return self._run_dynamic(skill, version, input_data, run, "error", exc, gate, solidify)

    def _run_dynamic(
        self,
        skill: ESkill,
        version: SkillVersion,
        input_data: dict[str, Any],
        run: SkillRun,
        reason: str,
        error: Exception | None,
        quality_gate: dict[str, Any],
        solidify: bool,
    ) -> SkillRun:
        self._record_event(
            skill_id=skill.skill_id,
            run_id=run.run_id,
            event_type="dynamic_triggered",
            stage="dynamic",
            trigger_signal=reason,
            strategy=self.adapter.__class__.__name__,
            details={"source_version": version.version, "error": str(error) if error else ""},
        )
        if not self._is_within_domain(skill, version.static_logic, input_data):
            run.fail("Dynamic phase rejected: input is outside skill domain", "domain_rejected")
            self.store.append_run(run)
            self.metrics.get_or_create(skill.skill_id).record_run("domain_rejected")
            self._record_event(
                skill_id=skill.skill_id,
                run_id=run.run_id,
                event_type="domain_rejected",
                stage=run.stage,
                trigger_signal=reason,
                strategy=self.adapter.__class__.__name__,
                details={"source_version": version.version},
            )
            return run
        history = self._retrieve_success_history(skill.skill_id, input_data)
        patch = self.adapter.propose(
            reason=reason,
            logic=version.static_logic,
            input_data=input_data,
            history=history,
            error=error,
        )
        self._record_event(
            skill_id=skill.skill_id,
            run_id=run.run_id,
            event_type="patch_generated",
            stage="dynamic",
            trigger_signal=reason,
            strategy=self.adapter.__class__.__name__,
            patch=patch.to_dict(),
            details={"history_matches": len(history), "source_version": version.version},
        )
        dynamic_logic = self._apply_patch(version.static_logic, patch)
        try:
            output = self._execute_static(dynamic_logic, input_data)
        except Exception as exc:  # noqa: BLE001
            rolled_back = self._rollback_to_previous_version(skill)
            run.patch = patch
            run.fail(str(exc), "rollback_or_ai_intervention")
            self.store.append_run(run)
            collector = self.metrics.get_or_create(skill.skill_id)
            collector.record_run("rollback_or_ai_intervention", str(exc))
            if rolled_back:
                collector.record_rollback()
            self._record_event(
                skill_id=skill.skill_id,
                run_id=run.run_id,
                event_type="rollback",
                stage=run.stage,
                trigger_signal=reason,
                strategy=self.adapter.__class__.__name__,
                patch=patch.to_dict(),
                details={"error": str(exc), "rolled_back": rolled_back},
            )
            return run

        quality = self._quality_report(output, quality_gate)
        if not quality["passed"]:
            rolled_back = self._rollback_to_previous_version(skill)
            patch.changes.setdefault("metadata", {})["quality_report"] = quality
            run.patch = patch
            run.complete(output, "rollback_or_ai_intervention")
            run.error = "Dynamic patch failed sandbox quality validation"
            self.store.append_run(run)
            collector = self.metrics.get_or_create(skill.skill_id)
            collector.record_run("rollback_or_ai_intervention", quality_score=quality["score"])
            if rolled_back:
                collector.record_rollback()
            self._record_event(
                skill_id=skill.skill_id,
                run_id=run.run_id,
                event_type="validation_failed",
                stage=run.stage,
                trigger_signal=reason,
                strategy=self.adapter.__class__.__name__,
                patch=patch.to_dict(),
                validation=quality,
                details={"rolled_back": rolled_back},
            )
            return run

        patch.changes.setdefault("metadata", {})["quality_report"] = quality
        run.patch = patch
        run.complete(output, "dynamic")
        self.metrics.get_or_create(skill.skill_id).record_run("dynamic", quality_score=quality["score"])
        self._record_event(
            skill_id=skill.skill_id,
            run_id=run.run_id,
            event_type="validation_passed",
            stage=run.stage,
            trigger_signal=reason,
            strategy=self.adapter.__class__.__name__,
            patch=patch.to_dict(),
            validation=quality,
            details={"source_version": version.version},
        )
        if solidify:
            next_version = SkillVersion(
                version=max(v.version for v in skill.versions) + 1,
                static_logic=dynamic_logic,
                trigger_policy=version.trigger_policy,
                quality_gate=version.quality_gate,
                source_run_id=run.run_id,
            )
            skill.add_version(next_version)
            self.store.save_skill(skill)
            run.stage = "solidified"
            run.output_data = {
                **run.output_data,
                "solidified_version": next_version.version,
            }
            collector = self.metrics.get_or_create(skill.skill_id)
            collector.record_version(next_version.version, run.run_id, reason)
            self._record_event(
                skill_id=skill.skill_id,
                run_id=run.run_id,
                event_type="version_solidified",
                stage=run.stage,
                trigger_signal=reason,
                strategy=self.adapter.__class__.__name__,
                patch=patch.to_dict(),
                validation=quality,
                solidified_version=next_version.version,
                details={"source_version": version.version},
            )
        else:
            self._record_event(
                skill_id=skill.skill_id,
                run_id=run.run_id,
                event_type="dynamic_completed",
                stage=run.stage,
                trigger_signal=reason,
                strategy=self.adapter.__class__.__name__,
                patch=patch.to_dict(),
                validation=quality,
                details={"solidify": False},
            )
        self.store.append_run(run)
        return run

    def get_metrics(self, skill_id: str | None = None) -> dict[str, Any]:
        if skill_id:
            return self.metrics.get_skill_report(skill_id) or {}
        return self.metrics.get_summary()

    def get_evolution_timeline(self, skill_id: str) -> list[dict[str, Any]]:
        events = self.store.list_events(skill_id)
        return sorted(events, key=lambda e: e.get("created_at", ""))

    def _execute_static(self, logic: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
        logic_type = logic.get("type") or "template_transform"
        required = [str(x) for x in logic.get("required_fields") or []]
        missing = [field for field in required if input_data.get(field) in (None, "")]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")

        if logic_type == "template_transform":
            rendered = self._render(str(logic.get("template") or ""), input_data)
            output_var = str(logic.get("output_var") or "result")
            return {output_var: rendered, "logic_type": logic_type}

        if logic_type == "employee_task":
            template = str(logic.get("task_template") or logic.get("task") or "")
            output_var = str(logic.get("output_var") or "employee_result")
            return {
                output_var: {
                    "task": self._render(template, input_data),
                    "simulated": True,
                },
                "logic_type": logic_type,
            }

        if logic_type == "pipeline":
            return self._execute_pipeline(logic, input_data)

        raise ValueError(f"Unsupported static logic type: {logic_type}")

    def _execute_pipeline(self, logic: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
        context = {**input_data}
        output: dict[str, Any] = {"logic_type": "pipeline"}
        steps = logic.get("steps") or []
        if not isinstance(steps, list):
            raise ValueError("pipeline steps must be a list")
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"pipeline step #{idx} must be an object")
            step_type = str(step.get("type") or "template_transform")
            output_var = str(step.get("output_var") or step.get("id") or f"step_{idx}")
            if step_type == "template_transform":
                value = self._render(str(step.get("template") or ""), context)
            elif step_type == "set_value":
                value = step.get("value")
            elif step_type == "tool_call":
                value = self._execute_tool_call(step, context)
            else:
                raise ValueError(f"Unsupported pipeline step type: {step_type}")
            output[output_var] = value
            context[output_var] = value
        return output

    def _execute_tool_call(self, step: dict[str, Any], context: dict[str, Any]) -> Any:
        tool = str(step.get("tool") or "")
        if tool == "echo":
            return step.get("args", context)
        if tool == "extract_keys":
            return sorted(context.keys())
        raise ValueError(f"Tool is not in ESkill allowlist: {tool}")

    def _passes_quality_gate(self, output: dict[str, Any], gate: dict[str, Any]) -> bool:
        return bool(self._quality_report(output, gate)["passed"])

    def _quality_report(self, output: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
        issues: list[str] = []
        score = 1.0
        min_length = int(gate.get("min_length") or 0)
        text = " ".join(
            str(v)
            for k, v in output.items()
            if k not in {"logic_type", "eskill_logic_type", "solidified_version"}
        )
        if min_length > 0 and len(text) < min_length:
            issues.append(f"min_length:{len(text)}<{min_length}")
            score = min(score, len(text) / max(min_length, 1))
        for key in [str(x) for x in gate.get("required_keys") or []]:
            if key not in output:
                issues.append(f"missing_key:{key}")
                score = min(score, 0.6)
        for token in [str(x) for x in gate.get("contains_all") or []]:
            if token and token not in text:
                issues.append(f"missing_text:{token}")
                score = min(score, 0.6)
        any_tokens = [str(x) for x in gate.get("contains_any") or []]
        if any_tokens and not any(token in text for token in any_tokens):
            issues.append("missing_any_text")
            score = min(score, 0.7)
        min_score = float(gate.get("min_score") or 0.0)
        return {"passed": not issues and score >= min_score, "score": round(score, 4), "issues": issues}

    def _apply_patch(self, logic: dict[str, Any], patch: DynamicPatch) -> dict[str, Any]:
        patched = deepcopy(logic)
        for key, value in patch.changes.items():
            if key == "metadata":
                meta = dict(patched.get("metadata") or {})
                meta.update(value if isinstance(value, dict) else {"value": value})
                patched["metadata"] = meta
            else:
                patched[key] = value
        return patched

    def _is_within_domain(
        self, skill: ESkill, logic: dict[str, Any], input_data: dict[str, Any]
    ) -> bool:
        keywords = logic.get("domain_keywords") or []
        if isinstance(keywords, str):
            keywords = [x.strip() for x in re.split(r"[,，\s]+", keywords) if x.strip()]
        if not keywords:
            keywords = [x for x in re.split(r"[,，\s]+", skill.domain or "") if len(x) >= 2]
        if not keywords:
            return True
        text = json.dumps(input_data, ensure_ascii=False).lower()
        return any(str(keyword).lower() in text for keyword in keywords)

    def _retrieve_success_history(
        self, skill_id: str, input_data: dict[str, Any], limit: int = 5
    ) -> list[dict[str, Any]]:
        text = json.dumps(input_data, ensure_ascii=False).lower()
        matches: list[dict[str, Any]] = []
        for row in reversed(self.store.list_runs(skill_id)):
            if row.get("stage") not in {"solidified", "dynamic"} or row.get("error"):
                continue
            prior_text = json.dumps(row.get("input_data") or {}, ensure_ascii=False).lower()
            if not text or not prior_text or set(text.split()) & set(prior_text.split()):
                patch = row.get("patch")
                if isinstance(patch, dict):
                    matches.append(patch)
            if len(matches) >= limit:
                break
        return matches

    def _rollback_to_previous_version(self, skill: ESkill) -> bool:
        previous = [v for v in skill.versions if v.version < skill.active_version]
        if not previous:
            return False
        skill.active_version = max(v.version for v in previous)
        self.store.save_skill(skill)
        return True

    def _render(self, template: str, values: dict[str, Any]) -> str:
        flat = {key: "" if value is None else str(value) for key, value in values.items()}
        rendered = Template(template).safe_substitute(flat)
        return re.sub(r"\s+", " ", rendered).strip()

    def _record_event(
        self,
        *,
        skill_id: str,
        event_type: str,
        run_id: str = "",
        stage: str = "",
        trigger_signal: str = "",
        strategy: str = "",
        patch: dict[str, Any] | None = None,
        validation: dict[str, Any] | None = None,
        solidified_version: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.store.append_event(
            EvolutionEvent(
                skill_id=skill_id,
                event_type=event_type,
                run_id=run_id,
                stage=stage,
                trigger_signal=trigger_signal,
                strategy=strategy,
                patch=patch,
                validation=validation,
                solidified_version=solidified_version,
                details=details or {},
            )
        )
