from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import DynamicPatch


class LLMPatchGenerator(ABC):
    @abstractmethod
    def generate_patch(
        self,
        *,
        reason: str,
        logic: dict[str, Any],
        input_data: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
        quality_report: dict[str, Any] | None = None,
    ) -> DynamicPatch:
        pass


class OpenAIPatchGenerator(LLMPatchGenerator):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def generate_patch(
        self,
        *,
        reason: str,
        logic: dict[str, Any],
        input_data: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
        quality_report: dict[str, Any] | None = None,
    ) -> DynamicPatch:
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package is required for LLMPatchGenerator. "
                "Install it with: pip install openai"
            )

        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = OpenAI(**client_kwargs)

        system_prompt = self._build_system_prompt(logic)
        user_prompt = self._build_user_prompt(reason, input_data, error, quality_report, history)

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or "{}"
        import json
        changes = json.loads(content)
        return DynamicPatch(reason=reason, changes=changes)

    def _build_system_prompt(self, logic: dict[str, Any]) -> str:
        return (
            "你是一个技能自适应补丁生成器。你的任务是分析技能执行失败的原因，"
            "并生成一个结构化补丁来修复问题。\n\n"
            f"当前技能逻辑配置：\n{logic}\n\n"
            "补丁应该是一个 JSON 对象，包含需要修改的字段和值。"
            "只输出 JSON，不要输出其他内容。"
        )

    def _build_user_prompt(
        self,
        reason: str,
        input_data: dict[str, Any],
        error: Exception | None,
        quality_report: dict[str, Any] | None,
        history: list[dict[str, Any]] | None,
    ) -> str:
        prompt = f"失败原因：{reason}\n\n"
        prompt += f"输入数据：{input_data}\n\n"
        if error:
            prompt += f"错误信息：{error}\n\n"
        if quality_report:
            prompt += f"质量报告：{quality_report}\n\n"
        if history:
            prompt += f"历史成功补丁：{history}\n\n"
        prompt += "请生成补丁来修复这个问题。"
        return prompt
