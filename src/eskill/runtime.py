from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Callable
from uuid import uuid4

from .analysis import analyze_static_logic
from .diagnostics import FaultClassifier, FaultLayer
from .metrics import RuntimeMetrics, SkillMetricsCollector
from .models import DynamicPatch, ESkill, EvolutionEvent, SkillRun, SkillVersion, TriggerPolicy
from .patch_planner import PatchPlanner
from .rollout import RolloutController, SelfHealingConfig
from .sandbox import SandboxRunner
from . import static_executor
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
    def __init__(
        self,
        store: JsonSkillStore,
        adapter: RuleBasedDynamicAdapter | None = None,
        *,
        healing: SelfHealingConfig | None = None,
        llm_generator: Any | None = None,
        self_healing_hook: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.store = store
        self.adapter = adapter or RuleBasedDynamicAdapter()
        self.metrics = RuntimeMetrics()
        self.healing = healing or SelfHealingConfig(enabled=False)
        self._patch_planner = PatchPlanner(rule_proposer=self.adapter, llm_generator=llm_generator)
        self._sandbox = SandboxRunner(timeout_seconds=self.healing.sandbox_timeout_seconds)
        self._rollout = RolloutController()
        self.on_version_solidified: Callable[[str, int], None] | None = None
        self.self_healing_hook = self_healing_hook

    def _resolve_version(self, skill: ESkill, input_data: dict[str, Any]) -> SkillVersion:
        if self.healing.enabled:
            return self._rollout.resolve_execution_version(skill, input_data)
        return skill.get_active_version()

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
        version = self._resolve_version(skill, input_data)
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
                if self.healing.enabled:
                    skill2 = self.store.get_skill(skill.skill_id)
                    used_candidate = version.version != skill2.active_version
                    self._rollout.record_outcome(skill2, used_candidate=used_candidate, success=True)
                    self._rollout.maybe_advance_progressive(skill2, self.healing)
                    self.store.save_skill(skill2)
                run.complete(output, "static")
                self.store.append_run(run)
                self.metrics.get_or_create(skill_id).record_run("static", quality_score=quality["score"])
                self._record_event(
                    skill_id=skill.skill_id,
                    run_id=run.run_id,
                    event_type="static_completed",
                    stage=run.stage,
                    validation=quality,
                    details={"active_version": skill.active_version, "executed_version": version.version},
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
            return self._run_dynamic(
                skill, version, input_data, run, reason, None, gate, solidify, static_quality=quality
            )
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
            return self._run_dynamic(
                skill, version, input_data, run, "error", exc, gate, solidify, static_quality=None
            )

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
        static_quality: dict[str, Any] | None = None,
    ) -> SkillRun:
        diagnosis = FaultClassifier.classify(
            trigger_reason=reason,
            error=error,
            quality_report=static_quality,
            static_logic=version.static_logic,
            input_data=input_data,
        )
        analysis: dict[str, Any] = {}
        if self.healing.enabled:
            analysis = analyze_static_logic(version.static_logic)
            run.diagnosis = diagnosis.to_dict()
            run.analysis_report = analysis
            self._record_event(
                skill_id=skill.skill_id,
                run_id=run.run_id,
                event_type="fault_diagnosed",
                stage="dynamic",
                trigger_signal=reason,
                strategy="FaultClassifier",
                details={"source_version": version.version},
                diagnosis=run.diagnosis,
                analysis_report=analysis,
            )
            if self.self_healing_hook is not None:
                self.self_healing_hook(
                    {
                        "event": "fault_diagnosed",
                        "skill_id": skill.skill_id,
                        "run_id": run.run_id,
                        "diagnosis": run.diagnosis,
                        "analysis_report": analysis,
                    }
                )

        self._record_event(
            skill_id=skill.skill_id,
            run_id=run.run_id,
            event_type="dynamic_triggered",
            stage="dynamic",
            trigger_signal=reason,
            strategy=self.adapter.__class__.__name__,
            details={
                "source_version": version.version,
                "error": str(error) if error else "",
                "fault_layer": diagnosis.layer.value,
            },
            diagnosis=run.diagnosis if self.healing.enabled else None,
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
        if self.healing.enabled:
            proposal = self._patch_planner.plan(
                diagnosis=diagnosis,
                analysis_report=analysis,
                reason=reason,
                logic=version.static_logic,
                input_data=input_data,
                history=history,
                error=error,
                quality_report=static_quality,
            )
            patch = proposal.patch
            planner_notes = proposal.to_dict()
        else:
            patch = self.adapter.propose(
                reason=reason,
                logic=version.static_logic,
                input_data=input_data,
                history=history,
                error=error,
            )
            planner_notes = {"planner": "rules_only"}
        self._record_event(
            skill_id=skill.skill_id,
            run_id=run.run_id,
            event_type="patch_generated",
            stage="dynamic",
            trigger_signal=reason,
            strategy=self.adapter.__class__.__name__,
            patch=patch.to_dict(),
            details={
                "history_matches": len(history),
                "source_version": version.version,
                "proposal": planner_notes,
            },
            diagnosis=run.diagnosis if self.healing.enabled else None,
        )
        dynamic_logic = self._apply_patch(version.static_logic, patch)

        need_subprocess_sandbox = self.healing.enabled and (
            diagnosis.layer in (FaultLayer.LOGIC, FaultLayer.ARCHITECTURE)
            or self.healing.sandbox_access_layer
        )
        if need_subprocess_sandbox:
            cases = self._build_sandbox_cases(version, input_data, quality_gate)
            regression = self._build_sandbox_regression_cases(version)
            sb = self._sandbox.validate(
                logic=dynamic_logic,
                gate=quality_gate,
                cases=cases,
                baseline_logic=version.static_logic,
                regression_cases=regression,
            )
            run.sandbox_summary = sb.to_dict()
            self._record_event(
                skill_id=skill.skill_id,
                run_id=run.run_id,
                event_type="sandbox_validation",
                stage="dynamic",
                trigger_signal=reason,
                strategy="SandboxRunner",
                details={"passed": sb.passed, "issues": sb.issues},
                sandbox_summary=run.sandbox_summary,
            )
            if self.self_healing_hook is not None:
                self.self_healing_hook(
                    {
                        "event": "sandbox_validation",
                        "skill_id": skill.skill_id,
                        "run_id": run.run_id,
                        "passed": sb.passed,
                        "sandbox_summary": run.sandbox_summary,
                    }
                )
            if not sb.passed:
                rolled_back = self._rollback_to_previous_version(skill)
                run.patch = patch
                run.fail("Subprocess sandbox validation failed", "rollback_or_ai_intervention")
                run.error = "; ".join(sb.issues) if sb.issues else "sandbox_failed"
                self.store.append_run(run)
                collector = self.metrics.get_or_create(skill.skill_id)
                collector.record_run("rollback_or_ai_intervention", run.error)
                if rolled_back:
                    collector.record_rollback()
                self._record_event(
                    skill_id=skill.skill_id,
                    run_id=run.run_id,
                    event_type="rollback",
                    stage=run.stage,
                    trigger_signal=reason,
                    strategy="SandboxRunner",
                    patch=patch.to_dict(),
                    details={"rolled_back": rolled_back, "sandbox": run.sandbox_summary},
                )
                return run

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
            sandbox_summary=run.sandbox_summary if run.sandbox_summary else None,
        )
        if solidify:
            next_version = SkillVersion(
                version=max(v.version for v in skill.versions) + 1,
                static_logic=dynamic_logic,
                trigger_policy=version.trigger_policy,
                quality_gate=version.quality_gate,
                source_run_id=run.run_id,
            )
            mode = (self.healing.rollout_mode or "immediate").lower()
            if self.healing.enabled and mode not in ("immediate", "", "none"):
                skill.add_version(next_version, activate=False)
                self._rollout.begin_rollout(
                    skill,
                    candidate_version=next_version.version,
                    mode=mode,
                    canary_percent=self.healing.canary_percent,
                )
                run.rollout_phase = str(skill.rollout.get("phase") or "")
                run.output_data = {
                    **output,
                    "solidified_version": next_version.version,
                    "candidate_version": next_version.version,
                    "rollout": dict(skill.rollout),
                }
            else:
                skill.add_version(next_version, activate=True)
                run.output_data = {**output, "solidified_version": next_version.version}
            self.store.save_skill(skill)
            run.stage = "solidified"
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
                details={
                    "source_version": version.version,
                    "rollout_mode": mode if self.healing.enabled else "immediate",
                    "rollout": dict(skill.rollout) if self.healing.enabled else {},
                },
                rollout_phase=run.rollout_phase,
            )
            if self.on_version_solidified is not None and skill.active_version == next_version.version:
                self.on_version_solidified(skill.skill_id, next_version.version)
            if self.self_healing_hook is not None:
                self.self_healing_hook(
                    {
                        "event": "version_solidified",
                        "skill_id": skill.skill_id,
                        "run_id": run.run_id,
                        "version": next_version.version,
                        "rollout": dict(skill.rollout),
                        "rollout_phase": run.rollout_phase,
                    }
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

    def _build_sandbox_cases(
        self, version: SkillVersion, input_data: dict[str, Any], gate: dict[str, Any]
    ) -> list[dict[str, Any]]:
        public_input = {k: v for k, v in input_data.items() if not str(k).startswith("_eskill")}
        cases: list[dict[str, Any]] = [
            {
                "case_id": "live_input",
                "input_data": public_input,
                "quality_gate": gate,
                "assert_partial_output": {},
            }
        ]
        meta = version.static_logic.get("metadata") if isinstance(version.static_logic.get("metadata"), dict) else {}
        extra = meta.get("sandbox_cases") if isinstance(meta, dict) else None
        if isinstance(extra, list):
            for i, row in enumerate(extra):
                if isinstance(row, dict):
                    cid = str(row.get("case_id") or f"sandbox_case_{i}")
                    cases.append({**row, "case_id": cid})
        return cases

    def _build_sandbox_regression_cases(self, version: SkillVersion) -> list[dict[str, Any]]:
        meta = version.static_logic.get("metadata") if isinstance(version.static_logic.get("metadata"), dict) else {}
        raw = meta.get("sandbox_regression") if isinstance(meta, dict) else None
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for i, row in enumerate(raw):
            if isinstance(row, dict) and row.get("input_data") is not None:
                out.append(
                    {
                        "case_id": str(row.get("case_id") or f"regression_{i}"),
                        "input_data": dict(row["input_data"]),
                    }
                )
        return out

    def get_metrics(self, skill_id: str | None = None) -> dict[str, Any]:
        if skill_id:
            return self.metrics.get_skill_report(skill_id) or {}
        return self.metrics.get_summary()

    def get_evolution_timeline(self, skill_id: str) -> list[dict[str, Any]]:
        events = self.store.list_events(skill_id)
        return sorted(events, key=lambda e: e.get("created_at", ""))

    def _execute_static(self, logic: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
        return static_executor.execute_static_logic(logic, input_data)

    def _passes_quality_gate(self, output: dict[str, Any], gate: dict[str, Any]) -> bool:
        return bool(self._quality_report(output, gate)["passed"])

    def _quality_report(self, output: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
        return static_executor.quality_report(output, gate)

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
        return static_executor.render_template(template, values)

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
        diagnosis: dict[str, Any] | None = None,
        analysis_report: dict[str, Any] | None = None,
        sandbox_summary: dict[str, Any] | None = None,
        rollout_phase: str = "",
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
                diagnosis=diagnosis,
                analysis_report=analysis_report,
                sandbox_summary=sandbox_summary,
                rollout_phase=rollout_phase or "",
            )
        )
