"""Natural-language → fully sandbox-validated workflow.

This is the headline feature: a single natural-language brief (e.g. "天气查询
员工，能查城市天气并给穿衣建议") is expanded into a multi-node graph where
every code-layer node is generated and verified before being returned. The
caller can hand the graph straight to :class:`VibeWorkflowEngine`, confident
that no node will fail at the *generation* layer (runtime failures still
trigger automatic patch + solidify).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .code_factory import NLCodeSkillFactory, VibeCodingError
from .config_factory import NLConfigSkillFactory
from .nl.llm import LLMClient
from .nl.prompts import WORKFLOW_PROMPT
from .workflow_models import (
    VibeWorkflowEdge,
    VibeWorkflowGraph,
    VibeWorkflowNode,
)


def _slug(value: str, fallback: str = "workflow") -> str:
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
            raise VibeCodingError(f"workflow LLM did not return JSON: {text[:200]!r}")
        data = json.loads(text[i : j + 1])
    if not isinstance(data, dict):
        raise VibeCodingError(f"workflow JSON is not an object: {type(data).__name__}")
    return data


@dataclass(slots=True)
class WorkflowGenerationReport:
    workflow_id: str
    graph: VibeWorkflowGraph
    code_skills_created: list[str]
    config_skills_created: list[str]
    warnings: list[str]


class NLWorkflowFactory:
    """Build a complete workflow + all referenced skills from a single brief.

    Constructor wiring:

    - ``llm`` produces the workflow JSON (one call)
    - ``code_factory`` is invoked once per code-layer node brief
    - ``config_factory`` is invoked once per config-layer node brief (optional)

    Both factories share their backing stores with the runtime / engine, so the
    generated workflow is immediately runnable.
    """

    def __init__(
        self,
        llm: LLMClient,
        code_factory: NLCodeSkillFactory,
        config_factory: NLConfigSkillFactory | None = None,
    ):
        self.llm = llm
        self.code_factory = code_factory
        self.config_factory = config_factory

    def generate(self, brief: str) -> VibeWorkflowGraph:
        return self.generate_with_report(brief).graph

    def generate_with_report(self, brief: str) -> WorkflowGenerationReport:
        if not brief or not brief.strip():
            raise VibeCodingError("workflow brief is required")

        raw = self.llm.chat(WORKFLOW_PROMPT, brief.strip(), json_mode=True)
        payload = _parse_json(raw)

        warnings: list[str] = []
        workflow_id = _slug(str(payload.get("workflow_id") or ""), fallback=f"wf-{uuid4().hex[:8]}")
        name = str(payload.get("name") or workflow_id).strip()
        domain = str(payload.get("domain") or "").strip()

        # 1. Generate code skills first so we can map temp_id → real skill_id
        code_briefs = payload.get("code_skills") or []
        if not isinstance(code_briefs, list):
            raise VibeCodingError("workflow.code_skills must be a list")

        temp_to_code_skill: dict[str, str] = {}
        code_skills_created: list[str] = []
        for idx, item in enumerate(code_briefs):
            if not isinstance(item, dict):
                warnings.append(f"code_skills[{idx}] is not an object, skipped")
                continue
            temp_id = str(item.get("temp_id") or item.get("id") or f"step{idx + 1}")
            skill_brief = str(item.get("skill_brief") or item.get("brief") or "").strip()
            if not skill_brief:
                warnings.append(f"code_skills[{temp_id}] has empty skill_brief, skipped")
                continue
            try:
                skill = self.code_factory.generate(
                    skill_brief,
                    skill_id=f"{workflow_id}-{temp_id}",
                )
            except VibeCodingError as exc:
                raise VibeCodingError(
                    f"failed to generate code skill {temp_id!r}: {exc}"
                ) from exc
            temp_to_code_skill[temp_id] = skill.skill_id
            code_skills_created.append(skill.skill_id)

        # 2. Generate config skills if any (currently the prompt restricts to code-only,
        #    but we keep the path open for future iterations)
        config_briefs = payload.get("config_skills") or []
        temp_to_config_skill: dict[str, str] = {}
        config_skills_created: list[str] = []
        if isinstance(config_briefs, list) and config_briefs:
            if self.config_factory is None:
                warnings.append("config_skills present but no config_factory provided; ignored")
            else:
                for idx, item in enumerate(config_briefs):
                    if not isinstance(item, dict):
                        continue
                    temp_id = str(item.get("temp_id") or f"cstep{idx + 1}")
                    sb = str(item.get("skill_brief") or "").strip()
                    if not sb:
                        warnings.append(f"config_skills[{temp_id}] empty, skipped")
                        continue
                    skill = self.config_factory.generate(
                        sb, skill_id=f"{workflow_id}-{temp_id}"
                    )
                    temp_to_config_skill[temp_id] = skill.skill_id
                    config_skills_created.append(skill.skill_id)

        # 3. Build nodes with mapped skill ids
        nodes_payload = payload.get("nodes") or []
        if not isinstance(nodes_payload, list) or not nodes_payload:
            raise VibeCodingError("workflow.nodes must be a non-empty list")

        nodes: list[VibeWorkflowNode] = []
        seen: set[str] = set()
        for raw_node in nodes_payload:
            if not isinstance(raw_node, dict):
                continue
            nid = str(raw_node.get("node_id") or "").strip()
            ntype = str(raw_node.get("node_type") or "").strip()
            if not nid or ntype not in ("start", "end", "eskill"):
                warnings.append(f"skipping invalid node {raw_node!r}")
                continue
            if nid in seen:
                warnings.append(f"duplicate node_id {nid!r}, skipped")
                continue
            seen.add(nid)

            layer = raw_node.get("layer")
            skill_ref = None
            code_skill_ref = None
            if ntype == "eskill":
                if layer not in ("config", "code"):
                    layer = "code"
                if layer == "code":
                    temp = str(
                        raw_node.get("code_skill_temp_id")
                        or raw_node.get("temp_id")
                        or raw_node.get("code_skill_ref")
                        or ""
                    )
                    if not temp:
                        raise VibeCodingError(
                            f"node {nid!r} layer=code has no code_skill_temp_id"
                        )
                    if temp not in temp_to_code_skill:
                        raise VibeCodingError(
                            f"node {nid!r} references unknown code_skill temp_id {temp!r}; "
                            f"known: {sorted(temp_to_code_skill)}"
                        )
                    code_skill_ref = temp_to_code_skill[temp]
                else:
                    temp = str(
                        raw_node.get("skill_temp_id")
                        or raw_node.get("temp_id")
                        or raw_node.get("skill_ref")
                        or ""
                    )
                    if not temp:
                        raise VibeCodingError(
                            f"node {nid!r} layer=config has no skill_temp_id"
                        )
                    if temp not in temp_to_config_skill:
                        raise VibeCodingError(
                            f"node {nid!r} references unknown config skill temp_id {temp!r}; "
                            f"known: {sorted(temp_to_config_skill)}"
                        )
                    skill_ref = temp_to_config_skill[temp]

            nodes.append(
                VibeWorkflowNode(
                    node_id=nid,
                    node_type=ntype,  # type: ignore[arg-type]
                    name=str(raw_node.get("name") or nid),
                    layer=layer if layer in ("config", "code") else None,  # type: ignore[arg-type]
                    skill_ref=skill_ref,
                    code_skill_ref=code_skill_ref,
                    config=dict(raw_node.get("config") or {}),
                )
            )

        edges_payload = payload.get("edges") or []
        edges: list[VibeWorkflowEdge] = []
        node_ids = {n.node_id for n in nodes}
        for raw_edge in edges_payload:
            if not isinstance(raw_edge, dict):
                continue
            src = str(raw_edge.get("source_node_id") or "").strip()
            tgt = str(raw_edge.get("target_node_id") or "").strip()
            if src not in node_ids or tgt not in node_ids:
                warnings.append(f"skipping edge {src!r} -> {tgt!r} (unknown node)")
                continue
            try:
                edges.append(
                    VibeWorkflowEdge(
                        source_node_id=src,
                        target_node_id=tgt,
                        condition=str(raw_edge.get("condition") or ""),
                    )
                )
            except ValueError as exc:
                warnings.append(f"skipping bad edge: {exc}")

        graph = VibeWorkflowGraph(
            workflow_id=workflow_id,
            name=name,
            domain=domain,
            nodes=nodes,
            edges=edges,
            metadata={
                "brief": brief.strip(),
                "code_skills_created": code_skills_created,
                "config_skills_created": config_skills_created,
            },
        )
        issues = graph.validate()
        if issues:
            raise VibeCodingError(f"generated workflow is invalid: {issues}")

        return WorkflowGenerationReport(
            workflow_id=workflow_id,
            graph=graph,
            code_skills_created=code_skills_created,
            config_skills_created=config_skills_created,
            warnings=warnings,
        )
