from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .models import EvolutionEvent, now_iso
from .runtime import ESkillRuntime
from .store import JsonSkillStore


@dataclass(slots=True)
class PipelineNode:
    node_id: str
    skill_id: str
    input_map: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ESkillPipeline:
    pipeline_id: str
    nodes: list[PipelineNode]
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "description": self.description,
            "nodes": [node.to_dict() for node in self.nodes],
        }


@dataclass(slots=True)
class PipelineRunResult:
    pipeline_id: str
    passed: bool
    node_outputs: dict[str, dict[str, Any]]
    failed_node: str = ""
    error: str = ""
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ESkillPipelineRunner:
    def __init__(self, store: JsonSkillStore, runtime: ESkillRuntime | None = None):
        self.store = store
        self.runtime = runtime or ESkillRuntime(store)

    def run(self, pipeline: ESkillPipeline, input_data: dict[str, Any]) -> PipelineRunResult:
        context: dict[str, Any] = dict(input_data)
        node_outputs: dict[str, dict[str, Any]] = {}

        for node in pipeline.nodes:
            mapped_input = self._map_input(node, context)
            run = self.runtime.run(node.skill_id, mapped_input, solidify=True)
            node_outputs[node.node_id] = dict(run.output_data)
            context[node.node_id] = dict(run.output_data)
            context.update(run.output_data)
            if run.error:
                result = PipelineRunResult(
                    pipeline_id=pipeline.pipeline_id,
                    passed=False,
                    node_outputs=node_outputs,
                    failed_node=node.node_id,
                    error=run.error,
                )
                self._record_pipeline_event(pipeline, node.node_id, False, result.to_dict())
                return result

        result = PipelineRunResult(
            pipeline_id=pipeline.pipeline_id,
            passed=True,
            node_outputs=node_outputs,
        )
        self._record_pipeline_event(pipeline, "", True, result.to_dict())
        return result

    def _map_input(self, node: PipelineNode, context: dict[str, Any]) -> dict[str, Any]:
        if not node.input_map:
            return dict(context)
        mapped: dict[str, Any] = {}
        for target_key, source_key in node.input_map.items():
            mapped[target_key] = self._resolve_path(context, source_key)
        return mapped

    def _resolve_path(self, context: dict[str, Any], source_key: str) -> Any:
        current: Any = context
        for part in source_key.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def _record_pipeline_event(
        self,
        pipeline: ESkillPipeline,
        failed_node: str,
        passed: bool,
        details: dict[str, Any],
    ) -> None:
        self.store.append_event(
            EvolutionEvent(
                skill_id=f"pipeline:{pipeline.pipeline_id}",
                event_type="pipeline_completed" if passed else "pipeline_failed",
                stage="pipeline",
                details={"failed_node": failed_node, **details},
            )
        )
