"""Natural-language → config-layer ESkill.

This factory is the simpler twin of :class:`NLCodeSkillFactory`. The config
layer (template_transform / employee_task / pipeline) was already covered by
:class:`eskill.llm_skill_author.OpenAISkillAuthor`; here we expose it through
the same ``LLMClient`` Protocol used by the rest of vibe coding so callers can
mix-and-match a single LLM client across factories.

The output is an :class:`ESkill` already saved to ``JsonSkillStore`` and
ready to be executed by :class:`ESkillRuntime`, which gives the caller config-
layer self-healing on top.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..llm_skill_author import OpenAISkillAuthor, blueprint_from_llm_payload
from ..models import ESkill
from ..skill_creator import SkillBlueprint, SkillCreator
from ..store import JsonSkillStore
from .nl.llm import LLMClient

_CONFIG_SYSTEM_PROMPT = """你是 ESkill 配置层技能设计师。根据用户需求生成可在 ESkillRuntime 中运行的配置层 Skill。

只输出一个 JSON 对象（不要 markdown 围栏、不要解释），结构如下：

{
  "skill_id": "kebab-case",
  "name": "中文 / 英文名称",
  "domain": "一句话能力边界",
  "static_logic": {
    "type": "template_transform",
    "template": "处理 ${var}",
    "dynamic_template": "动态分支模板，可选",
    "fallback_template": "兜底模板，可选",
    "required_fields": ["var"],
    "output_var": "result",
    "domain_keywords": ["关键词"]
  },
  "quality_gate": {"min_length": 0, "required_keys": []},
  "trigger_policy": {"on_error": true, "on_quality_below_threshold": true}
}

约束：
1. static_logic.type 仅限 template_transform / employee_task / pipeline
2. 优先 template_transform；employee_task 必须给 employee_id；pipeline 步骤里允许的 tool 只有 echo / extract_keys
3. required_fields 中列出的字段必须出现在 template / task_template 的 ${...} 占位符里
"""


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
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _parse_json(raw: str) -> dict[str, Any]:
    text = _strip_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            raise ValueError(f"LLM did not return JSON: {text[:200]!r}")
        data = json.loads(text[i : j + 1])
    if not isinstance(data, dict):
        raise ValueError(f"LLM JSON is not an object: {type(data).__name__}")
    return data


class NLConfigSkillFactory:
    """Generate a config-layer :class:`ESkill` from a natural-language brief.

    The factory accepts either a fresh :class:`LLMClient` (the vibe-coding
    abstraction) or a ready-made :class:`OpenAISkillAuthor`; the latter is the
    pre-existing path in eskill-prototype, kept for back-compat.
    """

    def __init__(
        self,
        llm: LLMClient | OpenAISkillAuthor,
        store: JsonSkillStore,
    ):
        self._llm = llm
        self.store = store

    def generate(self, brief: str, *, skill_id: str | None = None) -> ESkill:
        if not brief or not brief.strip():
            raise ValueError("brief is required")

        if isinstance(self._llm, OpenAISkillAuthor):
            blueprint = self._llm.draft_blueprint(brief, preferred_skill_id=skill_id)
        else:
            payload = self._call_llm_client(brief, skill_id=skill_id)
            if skill_id and not payload.get("skill_id"):
                payload["skill_id"] = _slug(skill_id, fallback="skill")
            blueprint = blueprint_from_llm_payload(payload)

        skill, _created = SkillCreator.ensure(self.store, blueprint)
        return skill

    def generate_blueprint(self, brief: str, *, skill_id: str | None = None) -> SkillBlueprint:
        """Like :meth:`generate` but returns the blueprint without persisting."""
        if isinstance(self._llm, OpenAISkillAuthor):
            return self._llm.draft_blueprint(brief, preferred_skill_id=skill_id)
        payload = self._call_llm_client(brief, skill_id=skill_id)
        if skill_id and not payload.get("skill_id"):
            payload["skill_id"] = _slug(skill_id, fallback="skill")
        return blueprint_from_llm_payload(payload)

    # ----------------------------------------------------------------- helpers

    def _call_llm_client(self, brief: str, *, skill_id: str | None) -> dict[str, Any]:
        user = brief.strip()
        if skill_id:
            user += f"\n\n建议使用 skill_id={_slug(skill_id, fallback='skill')!r}（可微调）"
        raw = self._llm.chat(_CONFIG_SYSTEM_PROMPT, user, json_mode=True)
        return _parse_json(raw)
