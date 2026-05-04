"""Employee 层 ESkill 包装器 —— 员工容器的自修复进化能力。

核心思想：
- AI Employee 是一个"壳"，包含 perception / memory / cognition / actions 四层
- 每一层都可以被 ESkill 化，获得自修复能力
- Employee 层的进化会影响旗下所有 Skill 的执行策略
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .errors import ESkillErrorCode
from .logging import _logger, get_logger, make_context
from .models import DynamicPatch, ESkill, SkillRun, SkillVersion, TriggerPolicy
from .runtime import ESkillRuntime, RuleBasedDynamicAdapter

logger = get_logger(__name__)


class EmployeeLayerConfig:
    """员工层配置，定义四层架构的自修复策略。"""

    def __init__(
        self,
        perception_enabled: bool = True,
        memory_enabled: bool = True,
        cognition_enabled: bool = True,
        actions_enabled: bool = True,
        quality_gate: dict[str, Any] | None = None,
        trigger_policy: dict[str, bool] | None = None,
    ):
        self.perception_enabled = perception_enabled
        self.memory_enabled = memory_enabled
        self.cognition_enabled = cognition_enabled
        self.actions_enabled = actions_enabled
        self.quality_gate = quality_gate or {}
        self.trigger_policy = trigger_policy or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "perception_enabled": self.perception_enabled,
            "memory_enabled": self.memory_enabled,
            "cognition_enabled": self.cognition_enabled,
            "actions_enabled": self.actions_enabled,
            "quality_gate": self.quality_gate,
            "trigger_policy": self.trigger_policy,
        }


class EmployeeLayerRunResult:
    """员工层执行结果，包含四层各自的执行状态和修复记录。"""

    def __init__(
        self,
        employee_id: str,
        task: str,
        perception_result: dict[str, Any],
        memory_result: dict[str, Any],
        cognition_result: dict[str, Any],
        actions_result: dict[str, Any],
        patches: list[DynamicPatch],
        duration_ms: float = 0.0,
    ):
        self.employee_id = employee_id
        self.task = task
        self.perception_result = perception_result
        self.memory_result = memory_result
        self.cognition_result = cognition_result
        self.actions_result = actions_result
        self.patches = patches
        self.duration_ms = duration_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "employee_id": self.employee_id,
            "task": self.task,
            "perception": self.perception_result,
            "memory": self.memory_result,
            "cognition": self.cognition_result,
            "actions": self.actions_result,
            "patches": [p.to_dict() for p in self.patches],
            "duration_ms": self.duration_ms,
            "total_patches": len(self.patches),
        }


class ESkillEmployeeWrapper:
    """员工层 ESkill 包装器。

    将 AI Employee 的四层架构包装为可自修复的 ESkill：
    - 感知层进化：输入解析失败时自动调整解析策略
    - 记忆层进化：记忆检索失败时调整检索参数
    - 认知层进化：LLM 调用失败时调整 Prompt/参数
    - 行动层进化：Action 执行失败时调整路由/参数

    同时，Skill 层的升级会传播到 Employee 层，影响执行策略。
    """

    def __init__(
        self,
        employee_id: str,
        store,
        layer_config: EmployeeLayerConfig | None = None,
        llm_generator=None,
        skill_runtimes: dict[str, ESkillRuntime] | None = None,
    ):
        self.employee_id = employee_id
        self.store = store
        self.layer_config = layer_config or EmployeeLayerConfig()
        self.llm_generator = llm_generator
        self.skill_runtimes = skill_runtimes or {}
        self._runtime = self._build_runtime()
        self._skill_upgrade_callbacks: list[Callable[[str, int], None]] = []
        self._healing_signals: list[dict[str, Any]] = []

    def register_skill_runtime(self, skill_id: str, runtime: ESkillRuntime) -> None:
        """注册 Skill 层运行时，建立双层关联。"""
        self.skill_runtimes[skill_id] = runtime
        runtime.on_version_solidified = self._on_skill_solidified

    def on_skill_upgrade(self, callback: Callable[[str, int], None]) -> None:
        """注册 Skill 升级回调，当 Skill 进化时通知 Employee 层。"""
        self._skill_upgrade_callbacks.append(callback)

    def execute(
        self,
        task: str,
        input_data: dict[str, Any],
        perception_fn: Callable,
        memory_fn: Callable,
        cognition_fn: Callable,
        actions_fn: Callable,
    ) -> EmployeeLayerRunResult:
        """执行员工任务，四层都有自修复能力。"""
        import time

        t0 = time.perf_counter()
        patches: list[DynamicPatch] = []

        ctx = make_context(_logger, employee_id=self.employee_id, task=task)

        input_data = dict(input_data)
        rid = input_data.get("request_id") or input_data.get("correlation_id")
        if rid:
            meta = dict(input_data.get("_eskill") or {})
            meta.setdefault("request_key", str(rid))
            input_data["_eskill"] = meta

        # 1. 感知层（带自修复）
        perceived, p_patch = self._run_layer(
            "perception",
            perception_fn,
            input_data,
            self.layer_config.perception_enabled,
        )
        if p_patch:
            patches.append(p_patch)

        # 2. 记忆层（带自修复）
        memory, m_patch = self._run_layer(
            "memory",
            memory_fn,
            {"perceived": perceived, "input_data": input_data},
            self.layer_config.memory_enabled,
        )
        if m_patch:
            patches.append(m_patch)

        # 3. 认知层（带自修复）
        reasoning, c_patch = self._run_layer(
            "cognition",
            cognition_fn,
            {"perceived": perceived, "memory": memory, "task": task},
            self.layer_config.cognition_enabled,
        )
        if c_patch:
            patches.append(c_patch)

        # 4. 行动层（带自修复）
        actions, a_patch = self._run_layer(
            "actions",
            actions_fn,
            {"reasoning": reasoning, "task": task},
            self.layer_config.actions_enabled,
        )
        if a_patch:
            patches.append(a_patch)

        duration_ms = round((time.perf_counter() - t0) * 1000, 3)

        # 保存执行记录
        self._save_employee_run(task, patches, duration_ms)

        return EmployeeLayerRunResult(
            employee_id=self.employee_id,
            task=task,
            perception_result=perceived,
            memory_result=memory,
            cognition_result=reasoning,
            actions_result=actions,
            patches=patches,
            duration_ms=duration_ms,
        )

    def _run_layer(
        self,
        layer_name: str,
        fn: Callable,
        input_data: dict[str, Any],
        enabled: bool,
    ) -> tuple[dict[str, Any], DynamicPatch | None]:
        """执行单一层，失败时尝试自修复。"""
        try:
            result = fn(input_data)
            if not isinstance(result, dict):
                result = {"output": result}

            # 质量检查
            if enabled and not self._check_layer_quality(layer_name, result):
                patch = self._try_heal_layer(layer_name, input_data, result, "quality_fail")
                if patch:
                    # 用修复后的逻辑重试
                    healed_result = self._apply_layer_patch(layer_name, fn, input_data, patch)
                    return healed_result, patch

            return result, None

        except Exception as e:
            logger.warning("[%s] %s 层执行失败: %s", self.employee_id, layer_name, e)

            if not enabled:
                raise

            patch = self._try_heal_layer(layer_name, input_data, {}, str(e))
            if patch:
                healed_result = self._apply_layer_patch(layer_name, fn, input_data, patch)
                return healed_result, patch

            raise

    def _check_layer_quality(self, layer_name: str, result: dict[str, Any]) -> bool:
        """检查单层执行质量。"""
        gate = self.layer_config.quality_gate.get(layer_name, {})
        if not gate:
            return True

        # 检查错误字段
        if result.get("error") or result.get("parse_error"):
            return False

        # 检查必需字段
        required = gate.get("required_keys", [])
        if required and not all(k in result for k in required):
            return False

        return True

    def _try_heal_layer(
        self,
        layer_name: str,
        input_data: dict[str, Any],
        result: dict[str, Any],
        trigger_signal: str,
    ) -> DynamicPatch | None:
        """尝试修复单层逻辑。"""
        logger.info("[%s] 尝试修复 %s 层: %s", self.employee_id, layer_name, trigger_signal)

        # 1. 尝试基于规则的修复
        rule_patch = self._rule_based_heal(layer_name, input_data, result, trigger_signal)
        if rule_patch:
            logger.info("[%s] %s 层规则修复成功", self.employee_id, layer_name)
            return rule_patch

        # 2. 如果有 LLM，尝试 LLM 修复
        if self.llm_generator:
            llm_patch = self._llm_heal_layer(layer_name, input_data, result, trigger_signal)
            if llm_patch:
                logger.info("[%s] %s 层 LLM 修复成功", self.employee_id, layer_name)
                return llm_patch

        return None

    def _rule_based_heal(
        self,
        layer_name: str,
        input_data: dict[str, Any],
        result: dict[str, Any],
        trigger_signal: str,
    ) -> DynamicPatch | None:
        """基于规则的层修复。"""
        changes: dict[str, Any] = {}

        if layer_name == "perception":
            if "parse_error" in result:
                # 尝试改变输入格式
                changes["fallback_input_format"] = "raw"
                changes["skip_normalization"] = True

        elif layer_name == "memory":
            if "error" in result:
                # 降级到短期记忆
                changes["fallback_to_short_term"] = True
                changes["disable_long_term"] = True

        elif layer_name == "cognition":
            if trigger_signal in ("missing api key", "llm call failed"):
                # 降级到本地推理
                changes["fallback_to_local"] = True
                changes["provider"] = "local"

        elif layer_name == "actions":
            if "error" in result:
                # 降级到 echo
                changes["fallback_handler"] = "echo"
                changes["skip_failed_handlers"] = True

        if changes:
            return DynamicPatch(
                reason=f"{layer_name}_rule_heal:{trigger_signal}",
                changes=changes,
            )
        return None

    def _llm_heal_layer(
        self,
        layer_name: str,
        input_data: dict[str, Any],
        result: dict[str, Any],
        trigger_signal: str,
    ) -> DynamicPatch | None:
        """使用 LLM 生成层修复补丁。"""
        if not self.llm_generator:
            return None

        prompt = self._build_layer_heal_prompt(layer_name, input_data, result, trigger_signal)
        try:
            patch_data = self.llm_generator.generate_patch(prompt, domain="employee_layer")
            if patch_data and patch_data.get("changes"):
                return DynamicPatch(
                    reason=f"{layer_name}_llm_heal:{trigger_signal}",
                    changes=patch_data["changes"],
                )
        except Exception as e:
            logger.warning("LLM 修复 %s 层失败: %s", layer_name, e)

        return None

    def _build_layer_heal_prompt(
        self,
        layer_name: str,
        input_data: dict[str, Any],
        result: dict[str, Any],
        trigger_signal: str,
    ) -> str:
        """构建层修复的 LLM Prompt。"""
        return (
            f"Employee '{self.employee_id}' 的 {layer_name} 层执行失败。\n"
            f"触发信号: {trigger_signal}\n"
            f"输入数据: {input_data}\n"
            f"当前结果: {result}\n\n"
            f"请生成修复配置，返回 JSON 格式: {{'changes': {{...}}}}"
        )

    def _apply_layer_patch(
        self,
        layer_name: str,
        fn: Callable,
        input_data: dict[str, Any],
        patch: DynamicPatch,
    ) -> dict[str, Any]:
        """应用层修复补丁并重试。"""
        # 修改输入数据，加入修复参数
        healed_input = dict(input_data)
        healed_input["_heal_patch"] = patch.changes
        healed_input["_heal_layer"] = layer_name

        try:
            result = fn(healed_input)
            if not isinstance(result, dict):
                result = {"output": result}
            result["_healed"] = True
            result["_heal_reason"] = patch.reason
            return result
        except Exception as e:
            return {
                "error": str(e),
                "_healed": False,
                "_heal_reason": patch.reason,
            }

    def _on_skill_solidified(self, skill_id: str, version: int) -> None:
        """Skill 层固化新版本时的回调 —— 升级传播到 Employee 层。"""
        logger.info(
            "[%s] 收到 Skill 升级通知: %s -> v%d",
            self.employee_id,
            skill_id,
            version,
        )

        # 通知所有注册的回调
        for cb in self._skill_upgrade_callbacks:
            try:
                cb(skill_id, version)
            except Exception:
                pass

        # 调整 Employee 层的执行策略（例如：更信任新版本的 Skill）
        self._adapt_to_skill_upgrade(skill_id, version)

    def _adapt_to_skill_upgrade(self, skill_id: str, version: int) -> None:
        """根据 Skill 升级调整 Employee 层策略。"""
        # 示例：如果某个 Skill 升级了，Employee 可以在认知层提到这个信息
        logger.info(
            "[%s] 调整策略以适应 Skill %s 的新版本 %d",
            self.employee_id,
            skill_id,
            version,
        )

    def _save_employee_run(self, task: str, patches: list[DynamicPatch], duration_ms: float) -> None:
        """保存员工执行记录到存储。"""
        try:
            run = SkillRun(
                run_id=f"emp_{self.employee_id}_{task}",
                skill_id=f"employee:{self.employee_id}",
                stage="employee_layer",
                input_data={"task": task},
                output_data={
                    "patches_count": len(patches),
                    "duration_ms": duration_ms,
                },
            )
            self.store.append_run(run)
        except Exception as e:
            logger.warning("保存员工执行记录失败: %s", e)

    def _build_runtime(self) -> ESkillRuntime:
        """构建员工层运行时（用于兼容 ESkill 协议）。"""
        if self.llm_generator:
            adapter = _EmployeeLLMAdapter(self.llm_generator)
        else:
            adapter = RuleBasedDynamicAdapter()

        runtime = ESkillRuntime(self.store, adapter)
        return runtime

    def on_self_healing_signal(self, skill_id: str, signal: str, payload: dict[str, Any]) -> None:
        """Receive Skill-layer diagnosis / sandbox / rollout events from DualLayerBridge."""
        self._healing_signals.append(
            {"skill_id": skill_id, "signal": signal, "payload": dict(payload)}
        )

    def get_stats(self) -> dict[str, Any]:
        """获取员工层统计信息。"""
        return {
            "employee_id": self.employee_id,
            "layer_config": self.layer_config.to_dict(),
            "registered_skills": list(self.skill_runtimes.keys()),
            "llm_enabled": self.llm_generator is not None,
            "self_healing_signals": list(self._healing_signals[-20:]),
        }


class _EmployeeLLMAdapter:
    """员工层 LLM 适配器。"""

    def __init__(self, llm_generator):
        self.llm_generator = llm_generator

    def adapt(self, skill_id: str, input_data: dict[str, Any], error_context: dict[str, Any]) -> DynamicPatch:
        """生成动态补丁。"""
        prompt = error_context.get("prompt", "")
        patch_data = self.llm_generator.generate_patch(prompt, domain="employee")
        return DynamicPatch(
            reason=error_context.get("reason", "employee_llm_adapt"),
            changes=patch_data.get("changes", {}),
        )
