from __future__ import annotations

import json
from typing import Any

from .models import TriggerPolicy
from .skill_creator import SkillBlueprint, normalize_skill_id

_RUNTIME_SCHEMA_HINT = """
输出一个 JSON 对象（仅此一个对象，不要 markdown），字段如下：
{
  "skill_id": "小写英文、数字、短横线，建议 kebab-case",
  "name": "展示名称",
  "domain": "一句话领域描述（领域守卫与文档用）",
  "static_logic": {
    "type": "template_transform | employee_task | pipeline",
    ... 见下 ...
  },
  "quality_gate": { "min_length": 0, "required_keys": [], "contains_all": [], "contains_any": [], "min_score": 0 },
  "trigger_policy": { "on_error": true, "on_quality_below_threshold": true, "force_dynamic": false }
}

static_logic 约束（必须与 ESkillRuntime 一致）：
1) template_transform:
   { "type": "template_transform", "template": "支持 ${var} 占位符",
     "required_fields": ["var"], "output_var": "result",
     "domain_keywords": ["可选", "关键词"],
     "dynamic_template": "可选，失败时动态分支用的模板",
     "allow_steps": true }
2) employee_task:
   { "type": "employee_task", "task_template": "任务描述 ${topic}",
     "required_fields": ["topic"], "output_var": "employee_result",
     "domain_keywords": [] }
3) pipeline:
   { "type": "pipeline", "steps": [
       { "type": "template_transform", "template": "...", "output_var": "a" },
       { "type": "set_value", "value": "x", "output_var": "b" },
       { "type": "tool_call", "tool": "echo", "args": {}, "output_var": "c" }
     ], "domain_keywords": [] }
   允许的工具名 ONLY: echo, extract_keys

优先使用 template_transform，除非用户明确要求多步骤 pipeline。
"""


class OpenAISkillAuthor:
    """
    Optional LLM-backed draft of SkillBlueprint from natural language.
    Requires: pip install openai
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def draft_blueprint(
        self,
        user_request: str,
        *,
        preferred_skill_id: str | None = None,
    ) -> SkillBlueprint:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package is required for OpenAISkillAuthor. "
                "Install with: pip install openai"
            ) from exc

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = OpenAI(**client_kwargs)

        system = (
            "你是 ESkill 技能设计师。根据用户需求生成可在 ESkillRuntime 中运行的首版静态技能。\n"
            "必须严格遵守结构化 schema，不要生成任意 Python 代码或 shell。\n"
            f"{_RUNTIME_SCHEMA_HINT}"
        )
        user = user_request.strip()
        if preferred_skill_id:
            user += f"\n\n优先 skill_id（可微调）：{preferred_skill_id}"

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        payload = json.loads(raw)
        return blueprint_from_llm_payload(payload)

    def draft_blueprint_json(self, user_request: str, **kwargs: Any) -> dict[str, Any]:
        """Same as draft_blueprint but returns validated manifest + static_logic dict for APIs."""
        bp = self.draft_blueprint(user_request, **kwargs)
        return {"manifest": bp.to_manifest(), "static_logic": dict(bp.static_logic)}


def blueprint_from_llm_payload(payload: dict[str, Any]) -> SkillBlueprint:
    """Parse LLM JSON into SkillBlueprint; raises ValueError on invalid shapes."""
    if not isinstance(payload, dict):
        raise ValueError("LLM payload must be a dict")

    sid = str(payload.get("skill_id") or "").strip()
    if not sid:
        raise ValueError("LLM payload missing skill_id")
    try:
        sid = normalize_skill_id(sid)
    except ValueError:
        sid = normalize_skill_id(f"skill-{sid}")

    name = str(payload.get("name") or sid).strip()
    domain = str(payload.get("domain") or name).strip()
    logic = payload.get("static_logic")
    if not isinstance(logic, dict):
        raise ValueError("static_logic must be an object")

    qg = payload.get("quality_gate") or {}
    if not isinstance(qg, dict):
        raise ValueError("quality_gate must be an object")

    tp_raw = payload.get("trigger_policy") or {}
    trigger_policy = TriggerPolicy.from_dict(tp_raw if isinstance(tp_raw, dict) else {})

    dynamic_template = None
    allow_dynamic_steps = None
    if isinstance(logic.get("dynamic_template"), str):
        dynamic_template = logic["dynamic_template"]
    if logic.get("allow_steps") is True:
        allow_dynamic_steps = True

    return SkillBlueprint(
        skill_id=sid,
        name=name,
        domain=domain,
        static_logic=logic,
        trigger_policy=trigger_policy,
        quality_gate=dict(qg),
        dynamic_template=dynamic_template,
        allow_dynamic_steps=allow_dynamic_steps,
    )
