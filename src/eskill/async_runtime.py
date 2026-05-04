from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any
from uuid import uuid4

from . import static_executor
from .architecture import ArchitectureExecutor, ArchitectureProfile
from .errors import (
    DomainOutOfScopeError,
    MissingRequiredFieldsError,
    QualityCheckFailedError,
    SkillNotFoundError,
    StoreWriteError,
)
from .logging import log_error, log_skill_run, log_version_solidified
from .metrics import RuntimeMetrics
from .models import DynamicPatch, ESkill, SkillRun, SkillVersion, TriggerPolicy
from .resilience import RetryPolicy
from .store import JsonSkillStore


class AsyncESkillRuntime:
    """异步版本的 ESkillRuntime, 适用于 FastAPI/异步 Agent。"""

    def __init__(
        self,
        store: JsonSkillStore,
        adapter: Any | None = None,
        retry_policy: RetryPolicy | None = None,
        timeout_seconds: float = 30.0,
    ):
        self.store = store
        self.adapter = adapter
        self.metrics = RuntimeMetrics()
        self.retry_policy = retry_policy or RetryPolicy()
        self.timeout_seconds = timeout_seconds
        self._arch_executor = ArchitectureExecutor()

    async def run(
        self,
        skill_id: str,
        input_data: dict[str, Any],
        *,
        trigger_policy: TriggerPolicy | None = None,
        quality_gate: dict[str, Any] | None = None,
        force_dynamic: bool = False,
        solidify: bool = True,
    ) -> SkillRun:
        try:
            skill = await asyncio.wait_for(
                asyncio.to_thread(self.store.get_skill, skill_id),
                timeout=self.timeout_seconds,
            )
        except KeyError:
            raise SkillNotFoundError(skill_id) from None
        except asyncio.TimeoutError:
            raise StoreWriteError("Skill read timeout") from None

        version = skill.get_active_version()
        policy = trigger_policy or version.trigger_policy
        gate = quality_gate or version.quality_gate

        run = SkillRun(
            run_id=uuid4().hex,
            skill_id=skill.skill_id,
            stage="static",
            input_data=deepcopy(input_data),
        )

        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(self._execute_static, skill.skill_id, version.static_logic, input_data),
                timeout=self.timeout_seconds,
            )
            quality = self._quality_report(output, gate)
            quality_ok = bool(quality["passed"])

            if quality_ok and not policy.force_dynamic and not force_dynamic:
                run.complete(output, "static")
                await asyncio.to_thread(self.store.append_run, run)
                self.metrics.get_or_create(skill_id).record_run("static", quality_score=quality["score"])
                log_skill_run(skill_id, run.run_id, "static", "Static execution completed", score=quality["score"])
                return run

            if not (policy.on_quality_below_threshold or policy.force_dynamic or force_dynamic):
                run.complete(output, "static_quality_failed")
                await asyncio.to_thread(self.store.append_run, run)
                self.metrics.get_or_create(skill_id).record_run("static_quality_failed")
                log_skill_run(skill_id, run.run_id, "static_quality_failed", "Quality gate failed")
                return run

            reason = "force_dynamic" if (policy.force_dynamic or force_dynamic) else "quality_gate"
            return await self._run_dynamic(
                skill, version, input_data, run, reason, None, gate, solidify, static_quality=quality
            )

        except MissingRequiredFieldsError:
            # 缺少必填字段是输入错误; 不应进入动态修复阶段
            raise
        except Exception as exc:
            if not policy.on_error:
                run.fail(str(exc), "static_error")
                await asyncio.to_thread(self.store.append_run, run)
                self.metrics.get_or_create(skill_id).record_run("static_error", str(exc))
                log_error(skill_id, run.run_id, str(exc), stage="static_error")
                return run
            return await self._run_dynamic(
                skill, version, input_data, run, "error", exc, gate, solidify, static_quality=None
            )

    async def _run_dynamic(
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
        _ = static_quality  # reserved for healing parity with ESkillRuntime
        if not self._is_within_domain(skill, version.static_logic, input_data):
            run.fail("Dynamic phase rejected: input is outside skill domain", "domain_rejected")
            await asyncio.to_thread(self.store.append_run, run)
            self.metrics.get_or_create(skill.skill_id).record_run("domain_rejected")
            raise DomainOutOfScopeError(skill.skill_id)

        history = self._retrieve_success_history(skill.skill_id, input_data)

        patch = await asyncio.wait_for(
            asyncio.to_thread(
                self.adapter.propose,
                reason=reason,
                logic=version.static_logic,
                input_data=input_data,
                history=history,
                error=error,
            ),
            timeout=self.timeout_seconds,
        )

        dynamic_logic = self._apply_patch(version.static_logic, patch)

        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(self._execute_static, skill.skill_id, dynamic_logic, input_data),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            rolled_back = await asyncio.to_thread(self._rollback_to_previous_version, skill)
            run.patch = patch
            run.fail(str(exc), "rollback_or_ai_intervention")
            await asyncio.to_thread(self.store.append_run, run)
            collector = self.metrics.get_or_create(skill.skill_id)
            collector.record_run("rollback_or_ai_intervention", str(exc))
            if rolled_back:
                collector.record_rollback()
            log_error(skill.skill_id, run.run_id, str(exc), stage="rollback")
            return run

        quality = self._quality_report(output, quality_gate)
        if not quality["passed"]:
            rolled_back = await asyncio.to_thread(self._rollback_to_previous_version, skill)
            patch.changes.setdefault("metadata", {})["quality_report"] = quality
            run.patch = patch
            run.complete(output, "rollback_or_ai_intervention")
            run.error = "Dynamic patch failed sandbox quality validation"
            await asyncio.to_thread(self.store.append_run, run)
            collector = self.metrics.get_or_create(skill.skill_id)
            collector.record_run("rollback_or_ai_intervention", quality_score=quality["score"])
            if rolled_back:
                collector.record_rollback()
            raise QualityCheckFailedError(quality.get("issues", []), quality["score"])

        patch.changes.setdefault("metadata", {})["quality_report"] = quality
        run.patch = patch
        run.complete(output, "dynamic")
        self.metrics.get_or_create(skill.skill_id).record_run("dynamic", quality_score=quality["score"])

        if solidify:
            next_version = SkillVersion(
                version=max(v.version for v in skill.versions) + 1,
                static_logic=dynamic_logic,
                trigger_policy=version.trigger_policy,
                quality_gate=version.quality_gate,
                source_run_id=run.run_id,
            )
            skill.add_version(next_version, activate=True)
            await asyncio.to_thread(self.store.save_skill, skill)
            run.stage = "solidified"
            run.output_data = {
                **run.output_data,
                "solidified_version": next_version.version,
            }
            collector = self.metrics.get_or_create(skill.skill_id)
            collector.record_version(next_version.version, run.run_id, reason)
            log_version_solidified(skill.skill_id, run.run_id, next_version.version)

        await asyncio.to_thread(self.store.append_run, run)
        return run

    def _execute_static(
        self, skill_id: str, logic: dict[str, Any], input_data: dict[str, Any]
    ) -> dict[str, Any]:
        required = [str(x) for x in logic.get("required_fields") or []]
        missing = [field for field in required if input_data.get(field) in (None, "")]
        if missing:
            raise MissingRequiredFieldsError(missing)

        raw = logic.get("architecture_profile")
        profile = ArchitectureProfile.from_dict(raw) if raw else None

        def do_run() -> dict[str, Any]:
            return static_executor.execute_static_logic(logic, input_data)

        if profile is None:
            return do_run()
        return self._arch_executor.execute(
            skill_id,
            profile,
            do_run,
            input_data=input_data,
            on_event=None,
        )

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

    def _is_within_domain(self, skill: ESkill, logic: dict[str, Any], input_data: dict[str, Any]) -> bool:
        import re
        keywords = logic.get("domain_keywords") or []
        if isinstance(keywords, str):
            keywords = [x.strip() for x in re.split(r"[\s,\uFF0C]+", keywords) if x.strip()]
        if not keywords:
            keywords = [x for x in re.split(r"[\s,\uFF0C]+", skill.domain or "") if len(x) >= 2]
        if not keywords:
            return True
        text = json.dumps(input_data, ensure_ascii=False).lower()
        return any(str(keyword).lower() in text for keyword in keywords)

    def _retrieve_success_history(self, skill_id: str, input_data: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
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

    def get_metrics(self, skill_id: str | None = None) -> dict[str, Any]:
        if skill_id:
            return self.metrics.get_skill_report(skill_id) or {}
        return self.metrics.get_summary()
