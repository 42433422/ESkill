"""Code-layer self-healing runtime."""

from __future__ import annotations

import builtins
import json
import re
from copy import deepcopy
from typing import Any, Protocol
from uuid import uuid4

from .. import static_executor
from ..models import EvolutionEvent, TriggerPolicy
from .diagnostics import CodeDiagnostics
from .models import (
    CodeSkillRun,
    CodeSkillVersion,
    CodeTestResult,
)
from .patch_generator import CodePatchGenerator, RuleBasedCodePatchGenerator
from .sandbox import CodeSandbox
from .store import JsonCodeSkillStore
from .validator import CodeValidator


class EventStore(Protocol):
    def append_event(self, event: EvolutionEvent) -> None: ...


class CodeSkillRuntime:
    """Execute code skills with automatic patching and solidification."""

    def __init__(
        self,
        code_store: JsonCodeSkillStore,
        *,
        llm_generator: CodePatchGenerator | None = None,
        event_store: EventStore | None = None,
        sandbox: CodeSandbox | None = None,
        validator: CodeValidator | None = None,
        diagnostics: CodeDiagnostics | None = None,
        rule_generator: CodePatchGenerator | None = None,
    ):
        self.store = code_store
        self.llm_generator = llm_generator
        self.event_store = event_store
        self.sandbox = sandbox or CodeSandbox()
        self.validator = validator or CodeValidator()
        self.diagnostics = diagnostics or CodeDiagnostics()
        self.rule_generator = rule_generator or RuleBasedCodePatchGenerator()

    def run(
        self,
        skill_id: str,
        input_data: dict[str, Any],
        *,
        force_dynamic: bool = False,
        solidify: bool = True,
        trigger_policy: TriggerPolicy | None = None,
        quality_gate: dict[str, Any] | None = None,
    ) -> CodeSkillRun:
        skill = self.store.get_code_skill(skill_id)
        version = skill.get_active_version()
        policy = trigger_policy or version.trigger_policy
        gate = quality_gate if quality_gate is not None else version.quality_gate

        run = CodeSkillRun(run_id=uuid4().hex, skill_id=skill_id, stage="static", input_data=deepcopy(input_data))
        self._emit_event(
            skill_id,
            run.run_id,
            "code_run_started",
            "static",
            {"active_version": version.version},
        )

        sb_result = self.sandbox.execute(version.source_code, version.function_name, input_data)

        if sb_result.success:
            quality = static_executor.quality_report(sb_result.output, gate)
            quality_ok = bool(quality.get("passed"))
            if quality_ok and not policy.force_dynamic and not force_dynamic:
                run.complete(sb_result.output, "static")
                self.store.append_code_run(run)
                self._emit_event(
                    skill_id,
                    run.run_id,
                    "code_static_completed",
                    run.stage,
                    {"validation": quality},
                )
                return run
            if (
                not quality_ok
                and not policy.on_quality_below_threshold
                and not policy.force_dynamic
                and not force_dynamic
            ):
                run.output_data = sb_result.output
                run.fail("; ".join(quality.get("issues") or []) or "quality_failed", "static_quality_failed")
                self.store.append_code_run(run)
                return run
        else:
            if not policy.on_error:
                run.fail(sb_result.error_message or "execute_failed", "static_error")
                self.store.append_code_run(run)
                return run

        if sb_result.success:
            q_issues = static_executor.quality_report(sb_result.output, gate).get("issues") or []
            qual_exc = ValueError("quality_gate:" + "; ".join(q_issues))
            diagnosis = self.diagnostics.diagnose(
                version.source_code,
                version.function_name,
                input_data,
                qual_exc,
                traceback_str="",
            )
        else:
            msg = sb_result.error_message or "sandbox_failed"
            et = (sb_result.error_type or "").strip()
            exc_cls = getattr(builtins, et, RuntimeError) if et else RuntimeError
            try:
                err = exc_cls(msg)
            except Exception:
                err = RuntimeError(msg)
            diagnosis = self.diagnostics.diagnose(
                version.source_code,
                version.function_name,
                input_data,
                err,
                traceback_str=sb_result.traceback_str or "",
            )

        run.diagnosis = diagnosis.to_dict()
        self._emit_event(
            skill_id,
            run.run_id,
            "code_fault_diagnosed",
            "dynamic",
            {},
            diagnosis=run.diagnosis,
        )

        if not self._is_within_domain(skill, version, input_data):
            run.fail("Dynamic phase rejected: input is outside skill domain", "domain_rejected")
            self.store.append_code_run(run)
            self._emit_event(skill_id, run.run_id, "code_domain_rejected", run.stage, {})
            return run

        patch = self.rule_generator.generate(
            version.source_code,
            version.function_name,
            version.signature,
            diagnosis,
            version.test_cases,
        )
        if patch is None and self.llm_generator is not None:
            patch = self.llm_generator.generate(
                version.source_code,
                version.function_name,
                version.signature,
                diagnosis,
                version.test_cases,
            )

        if not patch:
            run.fail("Unable to generate patch", "patch_failed")
            self.store.append_code_run(run)
            self._emit_event(skill_id, run.run_id, "code_patch_failed", run.stage, {})
            return run

        validation = self.validator.validate(
            patch.patched_code,
            function_name=version.function_name,
            signature=version.signature,
            dependencies=version.dependencies,
        )
        if not validation.safe:
            run.fail("; ".join(validation.issues), "validation_failed")
            self.store.append_code_run(run)
            self._emit_event(
                skill_id,
                run.run_id,
                "code_validation_failed",
                run.stage,
                {"issues": validation.issues},
            )
            return run

        test_results = self._run_test_cases(patch.patched_code, version)
        if not all(tr.passed for tr in test_results):
            failed = [tr.case_id for tr in test_results if not tr.passed]
            run.fail(f"test cases failed: {failed}", "test_failed")
            self.store.append_code_run(run)
            self._emit_event(skill_id, run.run_id, "code_test_failed", run.stage, {"failed": failed})
            return run

        verify = self.sandbox.execute(patch.patched_code, version.function_name, input_data)
        if not verify.success:
            run.fail(verify.error_message or "verify_execute_failed", "quality_failed")
            self.store.append_code_run(run)
            return run

        quality_v = static_executor.quality_report(verify.output, gate)
        if not quality_v.get("passed"):
            run.fail("; ".join(quality_v.get("issues") or []), "quality_failed")
            self.store.append_code_run(run)
            return run

        run.patch = patch
        run.output_data = verify.output
        run.stage = "dynamic"

        if solidify:
            new_version = CodeSkillVersion(
                version=version.version + 1,
                source_code=patch.patched_code,
                function_name=version.function_name,
                signature=version.signature,
                dependencies=list(version.dependencies),
                trigger_policy=version.trigger_policy,
                quality_gate=dict(version.quality_gate),
                test_cases=list(version.test_cases),
                domain_keywords=list(version.domain_keywords),
                source_run_id=run.run_id,
            )
            skill.add_version(new_version, activate=True)
            self.store.save_code_skill(skill)
            run.complete(run.output_data, "solidified")
            self._emit_event(
                skill_id,
                run.run_id,
                "code_version_solidified",
                run.stage,
                {"solidified_version": new_version.version},
                patch=patch.to_dict(),
            )
        else:
            run.complete(run.output_data, "dynamic")
            self._emit_event(skill_id, run.run_id, "code_dynamic_completed", run.stage, {})

        self.store.append_code_run(run)
        return run

    def _run_test_cases(self, source_code: str, version: CodeSkillVersion) -> list[CodeTestResult]:
        results: list[CodeTestResult] = []
        for tc in version.test_cases:
            r = self.sandbox.execute(source_code, version.function_name, tc.input_data)
            if not r.success:
                results.append(CodeTestResult(case_id=tc.case_id, passed=False, error=r.error_message))
                continue
            if tc.expected_output is None:
                results.append(CodeTestResult(case_id=tc.case_id, passed=True))
                continue
            if r.output != tc.expected_output:
                results.append(
                    CodeTestResult(
                        case_id=tc.case_id,
                        passed=False,
                        error=f"mismatch:{r.output}!={tc.expected_output}",
                    )
                )
            else:
                results.append(CodeTestResult(case_id=tc.case_id, passed=True))
        return results

    def _is_within_domain(
        self,
        skill: Any,
        version: CodeSkillVersion,
        input_data: dict[str, Any],
    ) -> bool:
        keywords = list(version.domain_keywords) if version.domain_keywords else []
        if not keywords and skill.domain:
            keywords = [x for x in re.split(r"[\s,\uFF0C]+", skill.domain or "") if len(x) >= 2]
        if not keywords:
            return True
        text = json.dumps(input_data, ensure_ascii=False).lower()
        return any(str(keyword).lower() in text for keyword in keywords)

    def _emit_event(
        self,
        skill_id: str,
        run_id: str,
        event_type: str,
        stage: str,
        details: dict[str, Any],
        *,
        diagnosis: dict[str, Any] | None = None,
        patch: dict[str, Any] | None = None,
    ) -> None:
        if self.event_store is None:
            return
        self.event_store.append_event(
            EvolutionEvent(
                skill_id=skill_id,
                event_type=event_type,
                run_id=run_id,
                stage=stage,
                details=details or {},
                diagnosis=diagnosis,
                patch=patch,
            )
        )
