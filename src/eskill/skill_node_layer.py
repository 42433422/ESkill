"""Skill 层 ESkill 包装器 —— 工作流节点的自修复进化能力。

核心思想：
- 工作流中的每个节点（employee, openapi, eskill 等）都是一个 Skill
- ESkill 节点包装器让这些节点获得自修复能力
- 节点级修复不影响其他节点，保持工作流的隔离性
- 节点升级可以传播到 Employee 层，影响整体策略
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .errors import ESkillErrorCode
from .logging import _logger, get_logger, make_context
from .models import DynamicPatch, ESkill, SkillRun, SkillVersion, TriggerPolicy
from .runtime import ESkillRuntime, RuleBasedDynamicAdapter
from .wrapper import ESkillWrapper

logger = get_logger(__name__)


class SkillNodeConfig:
    """工作流节点配置。"""

    def __init__(
        self,
        node_type: str,
        node_id: str,
        node_name: str = "",
        quality_gate: dict[str, Any] | None = None,
        trigger_policy: dict[str, bool] | None = None,
        fallback_strategy: str = "fail",  # fail / skip / default
        retry_count: int = 0,
        timeout_seconds: float = 30.0,
    ):
        self.node_type = node_type
        self.node_id = node_id
        self.node_name = node_name or node_id
        self.quality_gate = quality_gate or {}
        self.trigger_policy = trigger_policy or {}
        self.fallback_strategy = fallback_strategy
        self.retry_count = retry_count
        self.timeout_seconds = timeout_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "quality_gate": self.quality_gate,
            "trigger_policy": self.trigger_policy,
            "fallback_strategy": self.fallback_strategy,
            "retry_count": self.retry_count,
            "timeout_seconds": self.timeout_seconds,
        }


class SkillNodeRunResult:
    """节点执行结果。"""

    def __init__(
        self,
        node_id: str,
        node_type: str,
        success: bool,
        output: dict[str, Any],
        patch: DynamicPatch | None = None,
        stage: str = "",
        duration_ms: float = 0.0,
        retry_count: int = 0,
    ):
        self.node_id = node_id
        self.node_type = node_type
        self.success = success
        self.output = output
        self.patch = patch
        self.stage = stage
        self.duration_ms = duration_ms
        self.retry_count = retry_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "success": self.success,
            "output": self.output,
            "patch": self.patch.to_dict() if self.patch else None,
            "stage": self.stage,
            "duration_ms": self.duration_ms,
            "retry_count": self.retry_count,
        }


class ESkillNodeWrapper:
    """Skill 层 ESkill 包装器（工作流节点级）。

    将工作流中的任意节点包装为可自修复的 ESkill：
    - 节点执行失败 → 触发动态修复 → 重试
    - 节点输出质量不达标 → 触发适配 → 重试
    - 修复成功后固化新版本
    - 升级通知 Employee 层
    """

    def __init__(
        self,
        node_config: SkillNodeConfig,
        store,
        llm_generator=None,
        on_solidified: Callable[[str, int], None] | None = None,
    ):
        self.node_config = node_config
        self.store = store
        self.llm_generator = llm_generator
        self.on_solidified = on_solidified
        self._execution_count = 0
        self._success_count = 0
        self._heal_count = 0

    def execute(
        self,
        execute_fn: Callable[[dict[str, Any]], dict[str, Any]],
        input_data: dict[str, Any],
        workflow_context: dict[str, Any] | None = None,
    ) -> SkillNodeRunResult:
        """执行节点，带自修复能力。"""
        import time

        t0 = time.perf_counter()
        self._execution_count += 1

        node_id = self.node_config.node_id
        node_type = self.node_config.node_type
        ctx = make_context(_logger, node_id=node_id, node_type=node_type)

        # 确保节点有 ESkill 记录
        self._ensure_skill_record()

        # 第一次尝试
        result, patch, stage = self._try_execute(execute_fn, input_data, workflow_context)

        # 如果失败且有重试次数
        retry_count = 0
        if not result.get("_success") and self.node_config.retry_count > 0:
            for i in range(self.node_config.retry_count):
                retry_count += 1
                logger.info(
                    "[%s] 第 %d 次重试...",
                    node_id,
                    i + 1,
                )
                result, patch, stage = self._try_execute(
                    execute_fn, input_data, workflow_context, patch
                )
                if result.get("_success"):
                    break

        duration_ms = round((time.perf_counter() - t0) * 1000, 3)

        success = result.get("_success", True) and not result.get("error")
        if success:
            self._success_count += 1
        if patch:
            self._heal_count += 1

        # 清理内部标记
        output = {k: v for k, v in result.items() if not k.startswith("_")}

        # 保存运行记录
        self._save_run(input_data, output, stage, success, patch, duration_ms)

        return SkillNodeRunResult(
            node_id=node_id,
            node_type=node_type,
            success=success,
            output=output,
            patch=patch,
            stage=stage,
            duration_ms=duration_ms,
            retry_count=retry_count,
        )

    def _try_execute(
        self,
        execute_fn: Callable,
        input_data: dict[str, Any],
        workflow_context: dict[str, Any] | None,
        previous_patch: DynamicPatch | None = None,
    ) -> tuple[dict[str, Any], DynamicPatch | None, str]:
        """尝试执行节点，失败时修复。"""
        try:
            # 构建执行上下文
            exec_input = dict(input_data)
            if workflow_context:
                exec_input["_workflow_context"] = workflow_context
                rk = workflow_context.get("request_key") or workflow_context.get("correlation_id")
                if rk:
                    meta = dict(exec_input.get("_eskill") or {})
                    meta.setdefault("request_key", str(rk))
                    exec_input["_eskill"] = meta
            if previous_patch:
                exec_input["_previous_patch"] = previous_patch.changes

            output = execute_fn(exec_input)
            if not isinstance(output, dict):
                output = {"value": output}

            # 质量检查
            quality_ok = self._check_quality(output)
            if not quality_ok:
                # 尝试修复
                patch = self._heal_node(input_data, output, "quality_fail")
                if patch:
                    return self._apply_patch_and_retry(execute_fn, input_data, workflow_context, patch), patch, "healed"

            output["_success"] = True
            return output, previous_patch, "static"

        except Exception as e:
            logger.warning("[%s] 节点执行失败: %s", self.node_config.node_id, e)

            # 尝试修复
            patch = self._heal_node(input_data, {}, str(e))
            if patch:
                return self._apply_patch_and_retry(execute_fn, input_data, workflow_context, patch), patch, "healed"

            # 使用降级策略
            fallback = self._apply_fallback(str(e))
            fallback["_success"] = False
            return fallback, None, "fallback"

    def _check_quality(self, output: dict[str, Any]) -> bool:
        """检查节点输出质量。"""
        gate = self.node_config.quality_gate
        if not gate:
            return True

        # 检查错误
        if output.get("error") or output.get("failed"):
            return False

        # 检查必需字段
        required = gate.get("required_keys", [])
        if required and not all(k in output for k in required):
            return False

        # 检查最小分数
        min_score = gate.get("min_score")
        if min_score is not None:
            score = output.get("score", 0)
            if isinstance(score, (int, float)) and score < min_score:
                return False

        return True

    def _heal_node(
        self,
        input_data: dict[str, Any],
        output: dict[str, Any],
        trigger_signal: str,
    ) -> DynamicPatch | None:
        """修复节点逻辑。"""
        logger.info(
            "[%s] 尝试修复节点: %s",
            self.node_config.node_id,
            trigger_signal,
        )

        # 1. 规则修复
        rule_patch = self._rule_based_heal(input_data, output, trigger_signal)
        if rule_patch:
            return rule_patch

        # 2. LLM 修复
        if self.llm_generator:
            llm_patch = self._llm_heal(input_data, output, trigger_signal)
            if llm_patch:
                return llm_patch

        return None

    def _rule_based_heal(
        self,
        input_data: dict[str, Any],
        output: dict[str, Any],
        trigger_signal: str,
    ) -> DynamicPatch | None:
        """基于规则的节点修复。"""
        node_type = self.node_config.node_type
        changes: dict[str, Any] = {}

        if node_type == "employee":
            if "missing" in trigger_signal.lower():
                changes["add_default_params"] = True
            if "timeout" in trigger_signal.lower():
                changes["increase_timeout"] = True

        elif node_type == "openapi_operation":
            if "404" in trigger_signal or "not found" in trigger_signal.lower():
                changes["skip_missing_fields"] = True
            if "401" in trigger_signal or "403" in trigger_signal:
                changes["refresh_auth"] = True
            if "timeout" in trigger_signal.lower():
                changes["increase_timeout"] = True

        elif node_type == "eskill":
            if "quality" in trigger_signal.lower():
                changes["relax_quality_gate"] = True
            if "error" in trigger_signal.lower():
                changes["force_static"] = True

        elif node_type == "condition":
            if "eval" in trigger_signal.lower():
                changes["fallback_to_true"] = True

        if changes:
            return DynamicPatch(
                reason=f"node_rule_heal:{trigger_signal}",
                changes=changes,
            )
        return None

    def _llm_heal(
        self,
        input_data: dict[str, Any],
        output: dict[str, Any],
        trigger_signal: str,
    ) -> DynamicPatch | None:
        """使用 LLM 生成节点修复补丁。"""
        if not self.llm_generator:
            return None

        prompt = (
            f"工作流节点 '{self.node_config.node_name}' ({self.node_config.node_type}) 执行失败。\n"
            f"节点ID: {self.node_config.node_id}\n"
            f"触发信号: {trigger_signal}\n"
            f"输入: {input_data}\n"
            f"输出: {output}\n\n"
            f"请生成修复配置，返回 JSON: {{'changes': {{...}}}}"
        )

        try:
            patch_data = self.llm_generator.generate_patch(prompt, domain="workflow_node")
            if patch_data and patch_data.get("changes"):
                return DynamicPatch(
                    reason=f"node_llm_heal:{trigger_signal}",
                    changes=patch_data["changes"],
                )
        except Exception as e:
            logger.warning("LLM 修复节点失败: %s", e)

        return None

    def _apply_patch_and_retry(
        self,
        execute_fn: Callable,
        input_data: dict[str, Any],
        workflow_context: dict[str, Any] | None,
        patch: DynamicPatch,
    ) -> dict[str, Any]:
        """应用补丁并重试。"""
        healed_input = dict(input_data)
        healed_input["_heal_patch"] = patch.changes

        if workflow_context:
            healed_input["_workflow_context"] = workflow_context

        try:
            output = execute_fn(healed_input)
            if not isinstance(output, dict):
                output = {"value": output}
            output["_success"] = True
            output["_healed"] = True
            output["_heal_reason"] = patch.reason
            return output
        except Exception as e:
            return {
                "error": str(e),
                "_success": False,
                "_healed": False,
            }

    def _apply_fallback(self, error: str) -> dict[str, Any]:
        """应用降级策略。"""
        strategy = self.node_config.fallback_strategy

        if strategy == "skip":
            return {
                "skipped": True,
                "fallback_reason": error,
                "note": "节点被跳过，工作流继续",
            }
        elif strategy == "default":
            return {
                "default_value": True,
                "fallback_reason": error,
                "note": "使用默认值",
            }
        else:  # fail
            return {
                "error": error,
                "fallback_reason": error,
                "note": "节点执行失败",
            }

    def _ensure_skill_record(self) -> None:
        """确保节点在 ESkill 存储中有记录。"""
        skill_id = f"node:{self.node_config.node_id}"
        try:
            self.store.get_skill(skill_id)
        except KeyError:
            eskill = ESkill(
                skill_id=skill_id,
                name=self.node_config.node_name,
                domain=f"workflow:{self.node_config.node_type}",
                active_version=1,
                versions=[
                    SkillVersion(
                        version=1,
                        static_logic=self.node_config.to_dict(),
                        trigger_policy=TriggerPolicy.from_dict(self.node_config.trigger_policy),
                        quality_gate=self.node_config.quality_gate,
                    )
                ],
            )
            self.store.save_skill(eskill)

    def _save_run(
        self,
        input_data: dict[str, Any],
        output: dict[str, Any],
        stage: str,
        success: bool,
        patch: DynamicPatch | None,
        duration_ms: float,
    ) -> None:
        """保存节点运行记录。"""
        try:
            run = SkillRun(
                run_id=f"node_{self.node_config.node_id}_{self._execution_count}",
                skill_id=f"node:{self.node_config.node_id}",
                stage=stage,
                input_data=input_data,
                output_data=output,
                patch=patch,
                error="" if success else output.get("error", ""),
            )
            self.store.append_run(run)

            # 如果修复成功且需要固化
            if patch and success and self.node_config.trigger_policy.get("solidify_on_heal", True):
                self._solidify_version(patch)

        except Exception as e:
            logger.warning("保存节点运行记录失败: %s", e)

    def _solidify_version(self, patch: DynamicPatch) -> None:
        """将修复固化为新版本。"""
        skill_id = f"node:{self.node_config.node_id}"
        try:
            eskill = self.store.get_skill(skill_id)
            new_version_num = len(eskill.versions) + 1

            # 合并补丁到配置
            new_logic = self.node_config.to_dict()
            new_logic["heal_patches"] = new_logic.get("heal_patches", []) + [patch.changes]

            new_version = SkillVersion(
                version=new_version_num,
                static_logic=new_logic,
                trigger_policy=TriggerPolicy.from_dict(self.node_config.trigger_policy),
                quality_gate=self.node_config.quality_gate,
            )

            eskill.add_version(new_version, activate=True)
            self.store.save_skill(eskill)

            logger.info(
                "[%s] 节点修复已固化为版本 %d",
                self.node_config.node_id,
                new_version_num,
            )

            # 通知 Employee 层
            if self.on_solidified:
                self.on_solidified(skill_id, new_version_num)

        except Exception as e:
            logger.warning("固化节点版本失败: %s", e)

    def get_stats(self) -> dict[str, Any]:
        """获取节点统计。"""
        total = self._execution_count
        return {
            "node_id": self.node_config.node_id,
            "node_type": self.node_config.node_type,
            "total_executions": total,
            "success_count": self._success_count,
            "success_rate": (self._success_count / total * 100.0) if total > 0 else 0,
            "heal_count": self._heal_count,
            "config": self.node_config.to_dict(),
        }


class WorkflowESkillEngine:
    """工作流 ESkill 引擎 —— 管理所有节点的自修复。

    用法：
        engine = WorkflowESkillEngine(store)
        engine.register_node(node_config, execute_fn)
        result = engine.execute_node("node_1", input_data)
    """

    def __init__(self, store, llm_generator=None):
        self.store = store
        self.llm_generator = llm_generator
        self._nodes: dict[str, ESkillNodeWrapper] = {}
        self._execute_fns: dict[str, Callable] = {}
        self._on_node_solidified: Callable[[str, int], None] | None = None

    def set_on_node_solidified(self, callback: Callable[[str, int], None]) -> None:
        """设置节点固化回调（用于通知 Employee 层）。"""
        self._on_node_solidified = callback

    def register_node(
        self,
        node_config: SkillNodeConfig,
        execute_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """注册工作流节点。"""
        wrapper = ESkillNodeWrapper(
            node_config=node_config,
            store=self.store,
            llm_generator=self.llm_generator,
            on_solidified=self._on_node_solidified,
        )
        self._nodes[node_config.node_id] = wrapper
        self._execute_fns[node_config.node_id] = execute_fn

    def execute_node(
        self,
        node_id: str,
        input_data: dict[str, Any],
        workflow_context: dict[str, Any] | None = None,
    ) -> SkillNodeRunResult:
        """执行指定节点。"""
        if node_id not in self._nodes:
            raise ValueError(f"未注册的节点: {node_id}")

        wrapper = self._nodes[node_id]
        execute_fn = self._execute_fns[node_id]

        return wrapper.execute(execute_fn, input_data, workflow_context)

    def get_node_stats(self, node_id: str | None = None) -> dict[str, Any]:
        """获取节点统计。"""
        if node_id:
            if node_id not in self._nodes:
                return {}
            return self._nodes[node_id].get_stats()

        return {nid: n.get_stats() for nid, n in self._nodes.items()}

    def get_workflow_health(self) -> dict[str, Any]:
        """获取工作流整体健康度。"""
        stats = self.get_node_stats()
        total_execs = sum(s["total_executions"] for s in stats.values())
        total_success = sum(s["success_count"] for s in stats.values())
        total_heals = sum(s["heal_count"] for s in stats.values())

        return {
            "total_nodes": len(self._nodes),
            "total_executions": total_execs,
            "total_success": total_success,
            "overall_success_rate": (total_success / total_execs * 100.0) if total_execs > 0 else 0,
            "total_heals": total_heals,
            "node_stats": stats,
        }
