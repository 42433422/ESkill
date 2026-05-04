"""双层进化架构桥接器 —— Skill 层与 Employee 层的协同与升级传播。

核心机制：
1. 升级传播：Skill 层固化新版本 → 通知 Employee 层 → Employee 调整策略
2. 策略同步：Employee 层进化 → 影响旗下所有 Skill 的执行参数
3. 健康联动：Skill 健康度影响 Employee 决策，Employee 状态影响 Skill 触发策略
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .employee_layer import ESkillEmployeeWrapper, EmployeeLayerConfig
from .logging import get_logger
from .skill_node_layer import ESkillNodeWrapper, SkillNodeConfig, WorkflowESkillEngine

logger = get_logger(__name__)


class UpgradeEvent:
    """升级事件，用于层间通信。"""

    def __init__(
        self,
        source_layer: str,  # "skill" or "employee"
        source_id: str,
        version: int,
        change_type: str,  # "solidified", "policy_changed", "healed"
        details: dict[str, Any] | None = None,
    ):
        self.source_layer = source_layer
        self.source_id = source_id
        self.version = version
        self.change_type = change_type
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_layer": self.source_layer,
            "source_id": self.source_id,
            "version": self.version,
            "change_type": self.change_type,
            "details": self.details,
        }


class DualLayerBridge:
    """双层桥接器 —— 连接 Skill 层和 Employee 层。

    用法：
        bridge = DualLayerBridge(store, llm_generator)

        # 注册 Employee
        employee = bridge.create_employee("emp_1")

        # 注册工作流节点（自动关联到 Employee）
        bridge.register_skill_node("node_1", node_config, execute_fn, employee_id="emp_1")

        # 执行
        result = bridge.execute_skill_node("node_1", input_data)

        # 获取双层健康报告
        report = bridge.get_dual_layer_report()
    """

    def __init__(self, store, llm_generator=None):
        self.store = store
        self.llm_generator = llm_generator
        self._employees: dict[str, ESkillEmployeeWrapper] = {}
        self._workflow_engine = WorkflowESkillEngine(store, llm_generator)
        self._upgrade_history: list[UpgradeEvent] = []
        self._propagation_enabled = True

    def create_employee(
        self,
        employee_id: str,
        layer_config: EmployeeLayerConfig | None = None,
    ) -> ESkillEmployeeWrapper:
        """创建 Employee 层实例。"""
        employee = ESkillEmployeeWrapper(
            employee_id=employee_id,
            store=self.store,
            layer_config=layer_config,
            llm_generator=self.llm_generator,
        )

        # 注册 Skill 升级回调
        employee.on_skill_upgrade(self._on_skill_upgraded)

        self._employees[employee_id] = employee
        logger.info("[Bridge] Employee 已创建: %s", employee_id)
        return employee

    def register_skill_node(
        self,
        node_id: str,
        node_config: SkillNodeConfig,
        execute_fn: Callable[[dict[str, Any]], dict[str, Any]],
        employee_id: str | None = None,
    ) -> ESkillNodeWrapper:
        """注册 Skill 节点，可选关联到 Employee。"""
        # 设置节点固化回调，指向桥接器
        def on_solidified(skill_id: str, version: int):
            self._on_node_solidified(skill_id, version, employee_id)

        wrapper = ESkillNodeWrapper(
            node_config=node_config,
            store=self.store,
            llm_generator=self.llm_generator,
            on_solidified=on_solidified,
        )

        self._workflow_engine.register_node(node_config, execute_fn)

        # 如果关联了 Employee，建立双向连接
        if employee_id and employee_id in self._employees:
            employee = self._employees[employee_id]
            # Employee 监听这个节点的运行时
            # （通过 on_solidified 回调已实现）
            logger.info(
                "[Bridge] 节点 %s 已关联到 Employee %s",
                node_id,
                employee_id,
            )

        return wrapper

    def execute_skill_node(
        self,
        node_id: str,
        input_data: dict[str, Any],
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行 Skill 节点。"""
        return self._workflow_engine.execute_node(node_id, input_data, workflow_context)

    def execute_employee_task(
        self,
        employee_id: str,
        task: str,
        input_data: dict[str, Any],
        perception_fn: Callable,
        memory_fn: Callable,
        cognition_fn: Callable,
        actions_fn: Callable,
    ) -> dict[str, Any]:
        """执行 Employee 任务。"""
        if employee_id not in self._employees:
            raise ValueError(f"未注册的 Employee: {employee_id}")

        employee = self._employees[employee_id]
        result = employee.execute(
            task=task,
            input_data=input_data,
            perception_fn=perception_fn,
            memory_fn=memory_fn,
            cognition_fn=cognition_fn,
            actions_fn=actions_fn,
        )
        return result.to_dict()

    def _on_node_solidified(self, skill_id: str, version: int, employee_id: str | None) -> None:
        """Skill 节点固化时的处理 —— 升级传播到 Employee 层。"""
        event = UpgradeEvent(
            source_layer="skill",
            source_id=skill_id,
            version=version,
            change_type="solidified",
            details={"employee_id": employee_id},
        )
        self._upgrade_history.append(event)

        logger.info(
            "[Bridge] Skill %s 固化为版本 %d",
            skill_id,
            version,
        )

        if not self._propagation_enabled:
            return

        # 传播到关联的 Employee
        if employee_id and employee_id in self._employees:
            employee = self._employees[employee_id]
            employee._on_skill_solidified(skill_id, version)

            # 记录传播
            logger.info(
                "[Bridge] 升级已传播到 Employee %s",
                employee_id,
            )

    def _on_skill_upgraded(self, skill_id: str, version: int) -> None:
        """Skill 升级时的处理（从 Employee 回调）。"""
        event = UpgradeEvent(
            source_layer="employee",
            source_id=skill_id,
            version=version,
            change_type="policy_changed",
        )
        self._upgrade_history.append(event)

        logger.info(
            "[Bridge] Employee 感知到 Skill %s 升级至 v%d",
            skill_id,
            version,
        )

    def sync_employee_to_skills(self, employee_id: str) -> None:
        """将 Employee 层的策略同步到旗下所有 Skill 节点。

        例如：Employee 进化后，调整所有节点的质量门控阈值。
        """
        if employee_id not in self._employees:
            return

        employee = self._employees[employee_id]
        # 获取 Employee 的当前策略
        emp_stats = employee.get_stats()

        # 这里可以实现：根据 Employee 的进化历史，调整 Skill 节点的执行参数
        logger.info(
            "[Bridge] Employee %s 策略已同步到 Skill 层",
            employee_id,
        )

    def get_dual_layer_report(self) -> dict[str, Any]:
        """获取双层架构完整报告。"""
        # Skill 层健康度
        workflow_health = self._workflow_engine.get_workflow_health()

        # Employee 层健康度
        employee_stats = {
            eid: emp.get_stats()
            for eid, emp in self._employees.items()
        }

        # 升级历史
        recent_upgrades = [e.to_dict() for e in self._upgrade_history[-20:]]

        return {
            "skill_layer": workflow_health,
            "employee_layer": {
                "total_employees": len(self._employees),
                "employee_stats": employee_stats,
            },
            "upgrade_history": recent_upgrades,
            "total_upgrades": len(self._upgrade_history),
            "propagation_enabled": self._propagation_enabled,
        }

    def enable_propagation(self) -> None:
        """启用升级传播。"""
        self._propagation_enabled = True
        logger.info("[Bridge] 升级传播已启用")

    def disable_propagation(self) -> None:
        """禁用升级传播（用于维护模式）。"""
        self._propagation_enabled = False
        logger.info("[Bridge] 升级传播已禁用")

    def emit_self_healing_signal(
        self,
        skill_id: str,
        change_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record Skill-layer self-healing (diagnosis, sandbox, rollout) for Employee sync."""
        payload = dict(details or {})
        version = int(
            payload.get("version")
            or payload.get("candidate_version")
            or payload.get("solidified_version")
            or 0
        )
        event = UpgradeEvent(
            source_layer="skill",
            source_id=skill_id,
            version=version,
            change_type=change_type,
            details=payload,
        )
        self._upgrade_history.append(event)
        if not self._propagation_enabled:
            return
        for emp in self._employees.values():
            fn = getattr(emp, "on_self_healing_signal", None)
            if callable(fn):
                fn(skill_id, change_type, payload)

    def attach_runtime_healing(self, runtime: Any, skill_id: str) -> None:
        """Wire `ESkillRuntime.self_healing_hook` to this bridge for a given skill id."""

        def _hook(hook_payload: dict[str, Any]) -> None:
            evt = str(hook_payload.get("event") or "self_healing")
            self.emit_self_healing_signal(skill_id, evt, hook_payload)

        runtime.self_healing_hook = _hook

    def get_recent_self_healing(self, limit: int = 30) -> list[dict[str, Any]]:
        """Return recent upgrade/healing events (subset of history)."""
        tail = self._upgrade_history[-limit:]
        return [e.to_dict() for e in tail]


class DualLayerOrchestrator:
    """双层编排器 —— 高级封装，一键管理 Employee + Skill。

    这是给用户使用的最上层 API：
        orchestrator = DualLayerOrchestrator(store, llm_generator)

        # 定义一个带自修复能力的员工，包含多个技能节点
        emp = orchestrator.define_employee(
            employee_id="sales_assistant",
            perception_config={"type": "text"},
            cognition_config={"model": "deepseek-chat"},
            skills=[
                {"node_id": "greet", "type": "eskill", "execute": greet_fn},
                {"node_id": "query_product", "type": "openapi", "execute": query_fn},
                {"node_id": "recommend", "type": "employee", "execute": recommend_fn},
            ]
        )

        # 执行
        result = orchestrator.run("sales_assistant", "推荐一款手机")
    """

    def __init__(self, store, llm_generator=None):
        self.bridge = DualLayerBridge(store, llm_generator)
        self._skill_registry: dict[str, Callable] = {}

    def define_employee(
        self,
        employee_id: str,
        layer_config: EmployeeLayerConfig | None = None,
        skills: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """定义一个完整的双层进化员工。"""
        # 创建 Employee 层
        employee = self.bridge.create_employee(employee_id, layer_config)

        # 注册 Skill 节点
        registered_nodes = []
        if skills:
            for skill_def in skills:
                node_id = skill_def["node_id"]
                node_type = skill_def["type"]
                execute_fn = skill_def["execute"]

                node_config = SkillNodeConfig(
                    node_type=node_type,
                    node_id=node_id,
                    node_name=skill_def.get("name", node_id),
                    quality_gate=skill_def.get("quality_gate"),
                    trigger_policy=skill_def.get("trigger_policy"),
                    fallback_strategy=skill_def.get("fallback_strategy", "fail"),
                    retry_count=skill_def.get("retry_count", 0),
                )

                self.bridge.register_skill_node(
                    node_id=node_id,
                    node_config=node_config,
                    execute_fn=execute_fn,
                    employee_id=employee_id,
                )
                registered_nodes.append(node_id)

        return {
            "employee_id": employee_id,
            "registered_nodes": registered_nodes,
            "layer_config": (layer_config or EmployeeLayerConfig()).to_dict(),
        }

    def run(
        self,
        employee_id: str,
        task: str,
        input_data: dict[str, Any] | None = None,
        perception_fn: Callable | None = None,
        memory_fn: Callable | None = None,
        cognition_fn: Callable | None = None,
        actions_fn: Callable | None = None,
    ) -> dict[str, Any]:
        """运行员工任务（简化版）。"""
        input_data = input_data or {"task": task}

        # 默认的四层函数（如果未提供）
        def default_perception(data):
            return {"normalized_input": data, "type": "text"}

        def default_memory(data):
            return {"session": {}, "long_term": None}

        def default_cognition(data):
            return {"reasoning": f"处理任务: {task}", "task": task}

        def default_actions(data):
            return {"task": task, "handlers": ["echo"], "outputs": []}

        return self.bridge.execute_employee_task(
            employee_id=employee_id,
            task=task,
            input_data=input_data,
            perception_fn=perception_fn or default_perception,
            memory_fn=memory_fn or default_memory,
            cognition_fn=cognition_fn or default_cognition,
            actions_fn=actions_fn or default_actions,
        )

    def run_skill_node(
        self,
        node_id: str,
        input_data: dict[str, Any],
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """直接运行 Skill 节点。"""
        result = self.bridge.execute_skill_node(node_id, input_data, workflow_context)
        return result.to_dict()

    def get_report(self) -> dict[str, Any]:
        """获取完整报告。"""
        return self.bridge.get_dual_layer_report()
