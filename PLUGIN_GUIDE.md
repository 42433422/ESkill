# ESkill 插件开发指南

## 概述

ESkill 是一个可扩展的技能运行时框架。本指南说明如何开发自定义插件来扩展其功能。

## 扩展点

### 1. 自定义 Skill Adapter

实现 `SkillAdapter` 协议来支持新的技能类型：

```python
from eskill import SkillAdapter

class MySkillAdapter(SkillAdapter):
    def execute(self, input_data: dict) -> dict:
        # 实现你的技能逻辑
        return {"result": "your result"}

    def describe(self) -> dict:
        return {
            "skill_id": "my-skill",
            "name": "My Skill",
            "domain": "my-domain",
            "domain_keywords": ["keyword1", "keyword2"],
        }
```

### 2. 自定义 LLM Patch Generator

实现 `LLMPatchGenerator` 来使用不同的 LLM：

```python
from eskill import LLMPatchGenerator, DynamicPatch

class MyPatchGenerator(LLMPatchGenerator):
    def generate_patch(self, **kwargs) -> DynamicPatch:
        # 使用你的 LLM 生成补丁
        return DynamicPatch(reason=kwargs["reason"], changes={"template": "new template"})
```

### 3. 自定义工具

在 pipeline 中添加自定义工具：

```python
# 继承 RuleBasedDynamicAdapter
class MyAdapter(RuleBasedDynamicAdapter):
    def _execute_tool_call(self, step, context):
        tool = step.get("tool")
        if tool == "my_tool":
            return self._call_my_tool(step, context)
        return super()._execute_tool_call(step, context)
```

### 4. 自定义存储后端

实现与 `JsonSkillStore` 相同接口的存储后端：

```python
class MyCustomStore:
    def list_skills(self) -> list[ESkill]: ...
    def get_skill(self, skill_id: str) -> ESkill: ...
    def has_skill(self, skill_id: str) -> bool: ...
    def save_skill(self, skill: ESkill) -> None: ...
    def append_run(self, run: SkillRun) -> None: ...
    def list_runs(self, skill_id: str | None = None) -> list[dict]: ...
    def append_event(self, event: EvolutionEvent) -> None: ...
    def list_events(self, skill_id: str | None = None, event_type: str | None = None) -> list[dict]: ...
```

## 使用自定义存储

```python
from eskill import ESkillRuntime, MyCustomStore

store = MyCustomStore()
runtime = ESkillRuntime(store)
```

## 最佳实践

1. **线程安全**：所有存储操作必须是线程安全的
2. **错误处理**：使用 `ESkillError` 体系抛出统一错误
3. **日志**：使用 `eskill.logging` 记录操作日志
4. **配置**：通过 `ESkillConfig` 读取配置
