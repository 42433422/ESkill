"""Natural-language → sandbox-validated CodeSkill.

The factory is the heart of vibe coding: a single brief becomes a CodeSkill
that has *already* been validated by :class:`CodeValidator` and verified by
:class:`CodeSandbox` against the test cases the LLM itself produced. The
resulting skill is registered into a :class:`JsonCodeSkillStore`, which means
the existing :class:`CodeSkillRuntime` (with its diagnose-patch-solidify loop)
can be plugged in for runtime self-healing without further wiring.

Design choices:

- ``brief_first`` mode is on by default. The LLM is forced to write a spec
  (signature + test cases + quality gate) before being allowed to write code,
  which dramatically improves first-pass quality vs. one-shot generation.
- All LLM hops go through the :class:`LLMClient` Protocol so tests can swap in
  a deterministic :class:`MockLLM`.
- Repair rounds re-use the brief-first spec; we only ask the LLM to rewrite
  the function body, not the contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from ..code import (
    CodeFunctionSignature,
    CodeSandbox,
    CodeSkill,
    CodeSkillVersion,
    CodeTestCase,
    CodeValidator,
    JsonCodeSkillStore,
)
from ..code.validator import ALLOWED_IMPORT_MODULES
from ..models import TriggerPolicy
from .nl.llm import LLMClient
from .nl.prompts import (
    BRIEF_FIRST_CODE_PROMPT,
    BRIEF_FIRST_SPEC_PROMPT,
    CODE_DIRECT_PROMPT,
    CODE_REPAIR_PROMPT,
)

GenerationMode = Literal["direct", "brief_first"]


class VibeCodingError(RuntimeError):
    """Raised when generation fails after the configured number of repair rounds."""


@dataclass(slots=True)
class _Spec:
    """Internal: parsed LLM output before sandbox verification."""

    skill_id: str
    name: str
    domain: str
    function_name: str
    source_code: str
    signature: CodeFunctionSignature
    dependencies: list[str]
    test_cases: list[CodeTestCase]
    quality_gate: dict[str, Any]
    domain_keywords: list[str] = field(default_factory=list)


def _slug(value: str, fallback: str = "skill") -> str:
    s = re.sub(r"[\s_]+", "-", (value or "").strip().lower())
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        return fallback
    if len(s) > 64:
        s = s[:64].rstrip("-")
    return s or fallback


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json|python)?\s*", "", t, flags=re.I)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _parse_json(text: str) -> dict[str, Any]:
    raw = _strip_fence(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            raise VibeCodingError(f"LLM did not return JSON: {raw[:200]!r}")
        data = json.loads(raw[i : j + 1])
    if not isinstance(data, dict):
        raise VibeCodingError(f"LLM JSON is not an object: {type(data).__name__}")
    return data


class NLCodeSkillFactory:
    """Generate a sandbox-verified :class:`CodeSkill` from a natural-language brief.

    Lifecycle for one ``generate`` call:

    1. Resolve ``mode`` to either single-prompt or brief-first two-step
    2. Parse and normalize LLM output into ``_Spec``
    3. Validate with :class:`CodeValidator`; on failure ask the LLM to repair
       the function body and loop
    4. Run every test case in :class:`CodeSandbox`; on failure repair and loop
    5. After at most ``max_repair_rounds`` repairs, persist via the store
    6. Return the live ``CodeSkill`` (already in the store)
    """

    def __init__(
        self,
        llm: LLMClient,
        store: JsonCodeSkillStore,
        *,
        validator: CodeValidator | None = None,
        sandbox: CodeSandbox | None = None,
        max_repair_rounds: int = 3,
    ):
        self.llm = llm
        self.store = store
        self.validator = validator or CodeValidator()
        self.sandbox = sandbox or CodeSandbox()
        self.max_repair_rounds = int(max_repair_rounds)

    # ------------------------------------------------------------------ public

    def generate(
        self,
        brief: str,
        *,
        mode: GenerationMode = "brief_first",
        skill_id: str | None = None,
        dependencies: list[str] | None = None,
    ) -> CodeSkill:
        if not brief or not brief.strip():
            raise VibeCodingError("brief is required")

        spec = (
            self._brief_first(brief, dependencies)
            if mode == "brief_first"
            else self._direct(brief, dependencies)
        )
        if skill_id:
            spec.skill_id = _slug(skill_id, fallback=spec.skill_id)

        validated = self._iterate_until_safe(spec)
        return self._persist(validated)

    def repair(self, skill_id: str, failure: str) -> CodeSkill:
        """Public hook: ask the LLM to repair an existing skill given a failure note."""
        skill = self.store.get_code_skill(skill_id)
        version = skill.get_active_version()
        spec = _Spec(
            skill_id=skill.skill_id,
            name=skill.name,
            domain=skill.domain,
            function_name=version.function_name,
            source_code=version.source_code,
            signature=version.signature,
            dependencies=list(version.dependencies),
            test_cases=list(version.test_cases),
            quality_gate=dict(version.quality_gate),
            domain_keywords=list(version.domain_keywords),
        )
        repaired = self._repair_round(spec, [failure])
        validated = self._iterate_until_safe(repaired)
        # Append as a new version on top of the existing skill
        new_version = CodeSkillVersion(
            version=skill.active_version + 1,
            source_code=validated.source_code,
            function_name=validated.function_name,
            signature=validated.signature,
            dependencies=list(validated.dependencies),
            trigger_policy=version.trigger_policy,
            quality_gate=dict(validated.quality_gate),
            test_cases=list(validated.test_cases),
            domain_keywords=list(validated.domain_keywords),
            source_run_id=f"vibe-repair-{uuid4().hex[:8]}",
        )
        skill.add_version(new_version, activate=True)
        skill.domain = validated.domain or skill.domain
        self.store.save_code_skill(skill)
        return skill

    # -------------------------------------------------------------- generators

    def _direct(self, brief: str, deps: list[str] | None) -> _Spec:
        raw = self.llm.chat(CODE_DIRECT_PROMPT, brief, json_mode=True)
        payload = _parse_json(raw)
        return self._payload_to_spec(payload, default_deps=deps)

    def _brief_first(self, brief: str, deps: list[str] | None) -> _Spec:
        spec_raw = self.llm.chat(BRIEF_FIRST_SPEC_PROMPT, brief, json_mode=True)
        spec_payload = _parse_json(spec_raw)
        partial = self._payload_to_spec(spec_payload, default_deps=deps, allow_missing_code=True)

        code_user = (
            "规约如下，请严格按规约写函数体。\n\n"
            f"{json.dumps(spec_payload, ensure_ascii=False, indent=2)}"
        )
        code_raw = self.llm.chat(BRIEF_FIRST_CODE_PROMPT, code_user, json_mode=True)
        code_payload = _parse_json(code_raw)
        partial.source_code = str(code_payload.get("source_code") or "").strip()
        if not partial.source_code:
            raise VibeCodingError("brief-first stage 2 returned no source_code")
        return partial

    # ---------------------------------------------------------------- repair loop

    def _iterate_until_safe(self, spec: _Spec) -> _Spec:
        last_issues: list[str] = []
        for attempt in range(self.max_repair_rounds + 1):
            issues = self._collect_issues(spec)
            if not issues:
                return spec
            last_issues = issues
            if attempt == self.max_repair_rounds:
                break
            spec = self._repair_round(spec, issues)
        raise VibeCodingError(
            f"Failed to produce safe code after {self.max_repair_rounds} repair rounds; "
            f"last issues: {last_issues}"
        )

    def _collect_issues(self, spec: _Spec) -> list[str]:
        issues: list[str] = []
        validation = self.validator.validate(
            spec.source_code,
            function_name=spec.function_name,
            signature=spec.signature,
            dependencies=spec.dependencies,
        )
        if not validation.safe:
            issues.extend(f"validator:{x}" for x in validation.issues)
            return issues  # No point sandboxing if AST is unsafe

        for tc in spec.test_cases:
            result = self.sandbox.execute(spec.source_code, spec.function_name, tc.input_data)
            if not result.success:
                issues.append(
                    f"sandbox_failed:{tc.case_id}:{result.error_type}:{result.error_message}"
                )
                continue
            if tc.expected_output is None:
                continue
            if result.output != tc.expected_output:
                issues.append(
                    f"sandbox_mismatch:{tc.case_id}:{result.output}!={tc.expected_output}"
                )
        return issues

    def _repair_round(self, spec: _Spec, issues: list[str]) -> _Spec:
        user_msg = (
            "原代码：\n"
            f"```python\n{spec.source_code}\n```\n\n"
            "测试用例：\n"
            f"{json.dumps([tc.to_dict() for tc in spec.test_cases], ensure_ascii=False, indent=2)}\n\n"
            "失败信息：\n"
            f"{json.dumps(issues, ensure_ascii=False, indent=2)}\n\n"
            "保留函数名和参数，只改函数体；输出 JSON。"
        )
        raw = self.llm.chat(CODE_REPAIR_PROMPT, user_msg, json_mode=True)
        payload = _parse_json(raw)
        new_code = str(payload.get("source_code") or "").strip()
        if not new_code:
            raise VibeCodingError("Repair round returned no source_code")
        spec.source_code = new_code
        return spec

    # -------------------------------------------------------------- persistence

    def _persist(self, spec: _Spec) -> CodeSkill:
        if self.store.has_code_skill(spec.skill_id):
            existing = self.store.get_code_skill(spec.skill_id)
            new_version = CodeSkillVersion(
                version=existing.active_version + 1,
                source_code=spec.source_code,
                function_name=spec.function_name,
                signature=spec.signature,
                dependencies=list(spec.dependencies),
                trigger_policy=TriggerPolicy(),
                quality_gate=dict(spec.quality_gate),
                test_cases=list(spec.test_cases),
                domain_keywords=list(spec.domain_keywords),
                source_run_id=f"vibe-{uuid4().hex[:8]}",
            )
            existing.add_version(new_version, activate=True)
            existing.domain = spec.domain or existing.domain
            existing.name = spec.name or existing.name
            self.store.save_code_skill(existing)
            return existing

        version = CodeSkillVersion(
            version=1,
            source_code=spec.source_code,
            function_name=spec.function_name,
            signature=spec.signature,
            dependencies=list(spec.dependencies),
            trigger_policy=TriggerPolicy(),
            quality_gate=dict(spec.quality_gate),
            test_cases=list(spec.test_cases),
            domain_keywords=list(spec.domain_keywords),
            source_run_id=f"vibe-{uuid4().hex[:8]}",
        )
        skill = CodeSkill(
            skill_id=spec.skill_id,
            name=spec.name or spec.skill_id,
            domain=spec.domain,
            active_version=1,
            versions=[version],
        )
        self.store.save_code_skill(skill)
        return skill

    # -------------------------------------------------------------- normalize

    def _payload_to_spec(
        self,
        payload: dict[str, Any],
        *,
        default_deps: list[str] | None,
        allow_missing_code: bool = False,
    ) -> _Spec:
        function_name = str(payload.get("function_name") or "").strip()
        if not function_name:
            raise VibeCodingError("LLM payload missing function_name")
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", function_name):
            raise VibeCodingError(f"function_name {function_name!r} is not a valid Python identifier")

        signature_raw = payload.get("signature") or {}
        if not isinstance(signature_raw, dict):
            raise VibeCodingError("signature must be an object")
        signature = CodeFunctionSignature(
            params=[str(x) for x in signature_raw.get("params") or []],
            return_type=str(signature_raw.get("return_type") or "dict"),
            required_params=[str(x) for x in signature_raw.get("required_params") or []],
        )

        dependencies = [str(x) for x in payload.get("dependencies") or []]
        if default_deps:
            dependencies = list(dict.fromkeys([*dependencies, *default_deps]))
        # Filter out anything not in the validator allowlist; the AST step will
        # also catch this but we de-noise the output now.
        dependencies = [d for d in dependencies if d in ALLOWED_IMPORT_MODULES]

        raw_cases = payload.get("test_cases") or []
        if not isinstance(raw_cases, list) or not raw_cases:
            raise VibeCodingError("test_cases is required and must contain at least one case")
        test_cases: list[CodeTestCase] = []
        for idx, item in enumerate(raw_cases):
            if not isinstance(item, dict):
                continue
            cid = str(item.get("case_id") or item.get("id") or f"case_{idx + 1}")
            inp = item.get("input_data") or item.get("input") or {}
            if not isinstance(inp, dict):
                raise VibeCodingError(f"test_case[{cid}].input_data must be an object")
            exp = item.get("expected_output")
            if exp is not None and not isinstance(exp, dict):
                raise VibeCodingError(f"test_case[{cid}].expected_output must be an object or null")
            test_cases.append(
                CodeTestCase(case_id=cid, input_data=dict(inp), expected_output=dict(exp) if exp else None)
            )
        if not test_cases:
            raise VibeCodingError("test_cases collapsed to empty after normalization")

        source_code = str(payload.get("source_code") or "").strip()
        if not source_code and not allow_missing_code:
            raise VibeCodingError("source_code is required")

        quality_gate = payload.get("quality_gate") or {}
        if not isinstance(quality_gate, dict):
            raise VibeCodingError("quality_gate must be an object")

        domain_keywords = [str(x) for x in payload.get("domain_keywords") or []]
        skill_id = _slug(str(payload.get("skill_id") or ""), fallback=_slug(function_name))
        name = str(payload.get("name") or skill_id).strip()
        domain = str(payload.get("domain") or "").strip()

        return _Spec(
            skill_id=skill_id,
            name=name,
            domain=domain,
            function_name=function_name,
            source_code=source_code,
            signature=signature,
            dependencies=dependencies,
            test_cases=test_cases,
            quality_gate=dict(quality_gate),
            domain_keywords=domain_keywords,
        )
