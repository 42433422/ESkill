"""Execute a :class:`VibeWorkflowGraph`.

The engine is intentionally tiny: BFS from the start node, dispatch each
``eskill`` node to the appropriate self-healing runtime, accumulate outputs
into a context dict, and stop at the end node.

Self-healing is delegated to the underlying runtimes:

- ``layer="code"`` → :class:`CodeSkillRuntime` (sandbox + LLM patch + solidify)
- ``layer="config"`` → :class:`ESkillRuntime` (template patch + solidify)

Optionally each per-node call can be wrapped with
:class:`ESkillNodeWrapper` (set ``wrap_with_node_wrapper=True`` *and* provide
``node_wrapper_store``) to plug into the dual-layer bridge / audit trail used
elsewhere in eskill. Without a store the engine just calls the runtimes
directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..code import CodeSkillRuntime
from ..models import TriggerPolicy
from ..runtime import ESkillRuntime
from ..skill_node_layer import ESkillNodeWrapper, SkillNodeConfig
from ..store import JsonSkillStore
from .workflow_models import VibeWorkflowGraph, VibeWorkflowNode


@dataclass(slots=True)
class NodeRunOutcome:
    node_id: str
    layer: str | None
    skill_id: str
    stage: str
    output: dict[str, Any]
    duration_ms: float
    error: str = ""
    patch: dict[str, Any] | None = None


@dataclass(slots=True)
class WorkflowRunResult:
    workflow_id: str
    success: bool
    context: dict[str, Any] = field(default_factory=dict)
    outcomes: list[NodeRunOutcome] = field(default_factory=list)
    error: str = ""
    duration_ms: float = 0.0

    def output_for(self, node_id: str) -> dict[str, Any] | None:
        for o in self.outcomes:
            if o.node_id == node_id:
                return o.output
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "success": self.success,
            "context": dict(self.context),
            "outcomes": [
                {
                    "node_id": o.node_id,
                    "layer": o.layer,
                    "skill_id": o.skill_id,
                    "stage": o.stage,
                    "output": dict(o.output),
                    "duration_ms": o.duration_ms,
                    "error": o.error,
                    "patch": o.patch,
                }
                for o in self.outcomes
            ],
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class VibeWorkflowEngine:
    """Run a :class:`VibeWorkflowGraph` produced by :class:`NLWorkflowFactory`.

    At least one of ``code_runtime`` and ``config_runtime`` must be supplied.
    To enable :class:`ESkillNodeWrapper` for dual-layer integration, supply
    ``node_wrapper_store`` (a :class:`JsonSkillStore`) along with
    ``wrap_with_node_wrapper=True``.
    """

    def __init__(
        self,
        *,
        code_runtime: CodeSkillRuntime | None = None,
        config_runtime: ESkillRuntime | None = None,
        wrap_with_node_wrapper: bool = False,
        node_wrapper_store: JsonSkillStore | None = None,
    ):
        if code_runtime is None and config_runtime is None:
            raise ValueError("at least one of code_runtime / config_runtime is required")
        if wrap_with_node_wrapper and node_wrapper_store is None:
            raise ValueError("wrap_with_node_wrapper requires node_wrapper_store")
        self.code_runtime = code_runtime
        self.config_runtime = config_runtime
        self.wrap_with_node_wrapper = bool(wrap_with_node_wrapper)
        self.node_wrapper_store = node_wrapper_store

    def run(self, graph: VibeWorkflowGraph, input_data: dict[str, Any]) -> WorkflowRunResult:
        issues = graph.validate()
        if issues:
            return WorkflowRunResult(
                workflow_id=graph.workflow_id,
                success=False,
                error=f"graph validation failed: {issues}",
            )

        t0 = time.perf_counter()
        context: dict[str, Any] = dict(input_data or {})
        outcomes: list[NodeRunOutcome] = []
        try:
            order = graph.topological_order()
        except ValueError as exc:
            return WorkflowRunResult(
                workflow_id=graph.workflow_id, success=False, error=str(exc)
            )

        for node in order:
            if node.node_type in ("start", "end"):
                continue
            outcome = self._execute_node(node, context)
            outcomes.append(outcome)
            if outcome.error:
                return WorkflowRunResult(
                    workflow_id=graph.workflow_id,
                    success=False,
                    context=context,
                    outcomes=outcomes,
                    error=f"node {node.node_id!r} failed: {outcome.error}",
                    duration_ms=round((time.perf_counter() - t0) * 1000, 3),
                )
            output_var = str(node.config.get("output_var") or node.node_id)
            context[output_var] = outcome.output

        return WorkflowRunResult(
            workflow_id=graph.workflow_id,
            success=True,
            context=context,
            outcomes=outcomes,
            duration_ms=round((time.perf_counter() - t0) * 1000, 3),
        )

    # ------------------------------------------------------------------ helpers

    def _execute_node(self, node: VibeWorkflowNode, context: dict[str, Any]) -> NodeRunOutcome:
        if node.layer == "code":
            return self._execute_code_node(node, context)
        if node.layer == "config":
            return self._execute_config_node(node, context)
        return NodeRunOutcome(
            node_id=node.node_id,
            layer=node.layer,
            skill_id="",
            stage="invalid",
            output={},
            duration_ms=0.0,
            error=f"unsupported node layer {node.layer!r}",
        )

    def _execute_code_node(self, node: VibeWorkflowNode, context: dict[str, Any]) -> NodeRunOutcome:
        if self.code_runtime is None:
            return NodeRunOutcome(
                node_id=node.node_id,
                layer="code",
                skill_id=node.code_skill_ref or "",
                stage="missing_runtime",
                output={},
                duration_ms=0.0,
                error="code_runtime not configured",
            )
        skill_id = node.code_skill_ref or ""
        if not skill_id:
            return NodeRunOutcome(
                node_id=node.node_id,
                layer="code",
                skill_id="",
                stage="missing_skill",
                output={},
                duration_ms=0.0,
                error="code_skill_ref is empty",
            )
        input_data = self._build_input(node, context)
        t0 = time.perf_counter()
        wrapper = self._build_wrapper(node)
        try:
            if wrapper is not None:

                def _do_run(inp: dict[str, Any]) -> dict[str, Any]:
                    inner_inp = {k: v for k, v in inp.items() if not k.startswith("_")}
                    cr = self.code_runtime.run(
                        skill_id,
                        inner_inp,
                        force_dynamic=bool(node.config.get("force_dynamic", False)),
                        solidify=bool(node.config.get("solidify", True)),
                        trigger_policy=_to_trigger_policy(node.config.get("trigger_policy")),
                        quality_gate=node.config.get("quality_gate") or None,
                    )
                    self._last_code_run = cr
                    return cr.output_data if isinstance(cr.output_data, dict) else {"value": cr.output_data}

                wrapper_result = wrapper.execute(_do_run, input_data)
                cr = getattr(self, "_last_code_run", None)
                stage = cr.stage if cr is not None else wrapper_result.stage
                output = wrapper_result.output if isinstance(wrapper_result.output, dict) else {"value": wrapper_result.output}
                error = "" if wrapper_result.success else (wrapper_result.stage or "node_failed")
                patch_dict = wrapper_result.patch.to_dict() if wrapper_result.patch else (
                    cr.patch.to_dict() if cr and cr.patch else None
                )
            else:
                cr = self.code_runtime.run(
                    skill_id,
                    input_data,
                    force_dynamic=bool(node.config.get("force_dynamic", False)),
                    solidify=bool(node.config.get("solidify", True)),
                    trigger_policy=_to_trigger_policy(node.config.get("trigger_policy")),
                    quality_gate=node.config.get("quality_gate") or None,
                )
                stage = cr.stage
                output = cr.output_data if isinstance(cr.output_data, dict) else {"value": cr.output_data}
                error = cr.error
                patch_dict = cr.patch.to_dict() if cr.patch else None
        except Exception as exc:  # noqa: BLE001
            return NodeRunOutcome(
                node_id=node.node_id,
                layer="code",
                skill_id=skill_id,
                stage="exception",
                output={},
                duration_ms=round((time.perf_counter() - t0) * 1000, 3),
                error=str(exc),
            )
        return NodeRunOutcome(
            node_id=node.node_id,
            layer="code",
            skill_id=skill_id,
            stage=stage,
            output=dict(output) if output else {},
            duration_ms=round((time.perf_counter() - t0) * 1000, 3),
            error=error or "",
            patch=patch_dict,
        )

    def _execute_config_node(self, node: VibeWorkflowNode, context: dict[str, Any]) -> NodeRunOutcome:
        if self.config_runtime is None:
            return NodeRunOutcome(
                node_id=node.node_id,
                layer="config",
                skill_id=node.skill_ref or "",
                stage="missing_runtime",
                output={},
                duration_ms=0.0,
                error="config_runtime not configured",
            )
        skill_id = node.skill_ref or ""
        if not skill_id:
            return NodeRunOutcome(
                node_id=node.node_id,
                layer="config",
                skill_id="",
                stage="missing_skill",
                output={},
                duration_ms=0.0,
                error="skill_ref is empty",
            )
        input_data = self._build_input(node, context)
        t0 = time.perf_counter()
        try:
            sr = self.config_runtime.run(
                skill_id,
                input_data,
                force_dynamic=bool(node.config.get("force_dynamic", False)),
                solidify=bool(node.config.get("solidify", True)),
                trigger_policy=_to_trigger_policy(node.config.get("trigger_policy")),
                quality_gate=node.config.get("quality_gate") or None,
            )
        except Exception as exc:  # noqa: BLE001
            return NodeRunOutcome(
                node_id=node.node_id,
                layer="config",
                skill_id=skill_id,
                stage="exception",
                output={},
                duration_ms=round((time.perf_counter() - t0) * 1000, 3),
                error=str(exc),
            )
        return NodeRunOutcome(
            node_id=node.node_id,
            layer="config",
            skill_id=skill_id,
            stage=sr.stage,
            output=dict(sr.output_data) if sr.output_data else {},
            duration_ms=round((time.perf_counter() - t0) * 1000, 3),
            error=sr.error or "",
            patch=sr.patch.to_dict() if sr.patch else None,
        )

    def _build_input(self, node: VibeWorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
        input_mapping = node.config.get("input_mapping")
        if isinstance(input_mapping, dict) and input_mapping:
            mapped: dict[str, Any] = {}
            for k, ref in input_mapping.items():
                mapped[str(k)] = _resolve_ref(ref, context)
            return mapped
        return dict(context)

    def _build_wrapper(self, node: VibeWorkflowNode) -> ESkillNodeWrapper | None:
        if not self.wrap_with_node_wrapper or self.node_wrapper_store is None:
            return None
        return ESkillNodeWrapper(
            node_config=SkillNodeConfig(
                node_type="eskill",
                node_id=node.node_id,
                node_name=node.name or node.node_id,
                quality_gate=dict(node.config.get("quality_gate") or {}),
                trigger_policy=dict(node.config.get("trigger_policy") or {}),
                retry_count=int(node.config.get("retry_count") or 0),
            ),
            store=self.node_wrapper_store,
        )


def _resolve_ref(ref: Any, context: dict[str, Any]) -> Any:
    """Resolve an input_mapping value, supporting ``a.b.c`` dotted paths into
    the workflow context. Non-string refs and unresolved refs are returned as-is.
    """
    if not isinstance(ref, str):
        return ref
    if "." not in ref:
        return context[ref] if ref in context else ref
    parts = ref.split(".")
    head = parts[0]
    if head not in context:
        return ref
    cursor: Any = context[head]
    for part in parts[1:]:
        if isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            return ref
    return cursor


def _to_trigger_policy(raw: Any) -> TriggerPolicy | None:
    if raw is None:
        return None
    if isinstance(raw, TriggerPolicy):
        return raw
    if isinstance(raw, dict):
        return TriggerPolicy.from_dict(raw)
    return None
