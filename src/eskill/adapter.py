from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol


class SkillProtocol(Protocol):
    def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        ...


class SkillAdapter(ABC):
    @abstractmethod
    def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        pass

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        pass


class FunctionSkillAdapter(SkillAdapter):
    def __init__(
        self,
        func: Any,
        skill_id: str,
        name: str,
        domain: str,
        domain_keywords: list[str] | None = None,
    ):
        self.func = func
        self.skill_id = skill_id
        self.name = name
        self.domain = domain
        self.domain_keywords = domain_keywords or []

    def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        result = self.func(input_data)
        if not isinstance(result, dict):
            return {"result": result}
        return result

    def describe(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "domain": self.domain,
            "domain_keywords": self.domain_keywords,
        }


class DictSkillAdapter(SkillAdapter):
    def __init__(
        self,
        template: str,
        skill_id: str,
        name: str,
        domain: str,
        output_var: str = "result",
        required_fields: list[str] | None = None,
        domain_keywords: list[str] | None = None,
    ):
        self.template = template
        self.skill_id = skill_id
        self.name = name
        self.domain = domain
        self.output_var = output_var
        self.required_fields = required_fields or []
        self.domain_keywords = domain_keywords or []

    def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        missing = [f for f in self.required_fields if input_data.get(f) in (None, "")]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")
        rendered = self.template
        for key, value in input_data.items():
            placeholder = "${" + key + "}"
            rendered = rendered.replace(placeholder, str(value) if value is not None else "")
        return {self.output_var: rendered}

    def describe(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "domain": self.domain,
            "required_fields": self.required_fields,
            "domain_keywords": self.domain_keywords,
        }
