"""Dataclasses describing a vibe-coding workflow graph.

Kept deliberately small: nodes know their type, layer (config / code), and the
skill_id they reference; edges are directed with an optional condition string
that is currently informational only (we do not evaluate expressions yet).

The graph is an *output* of :class:`NLWorkflowFactory` and the *input* of
:class:`VibeWorkflowEngine`. Persisting it to JSON is supported via
``to_dict`` / ``from_dict`` to make round-tripping through the audit ledger
straightforward.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal

NodeType = Literal["start", "end", "eskill"]
NodeLayer = Literal["config", "code"]


@dataclass(slots=True)
class VibeWorkflowNode:
    node_id: str
    node_type: NodeType
    name: str = ""
    layer: NodeLayer | None = None
    skill_ref: str | None = None  # config-layer ESkill id
    code_skill_ref: str | None = None  # code-layer CodeSkill id
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.node_id = str(self.node_id).strip()
        if not self.node_id:
            raise ValueError("node_id is required")
        if self.node_type not in ("start", "end", "eskill"):
            raise ValueError(f"invalid node_type {self.node_type!r}")
        if self.node_type == "eskill":
            if self.layer not in ("config", "code"):
                raise ValueError(f"eskill node {self.node_id!r} requires layer config|code")
            if self.layer == "config" and not self.skill_ref:
                raise ValueError(f"eskill node {self.node_id!r} layer=config needs skill_ref")
            if self.layer == "code" and not self.code_skill_ref:
                raise ValueError(f"eskill node {self.node_id!r} layer=code needs code_skill_ref")
        self.name = (self.name or self.node_id).strip()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VibeWorkflowNode:
        layer = raw.get("layer")
        return cls(
            node_id=str(raw.get("node_id") or ""),
            node_type=str(raw.get("node_type") or "eskill"),  # type: ignore[arg-type]
            name=str(raw.get("name") or ""),
            layer=layer if layer in ("config", "code") else None,  # type: ignore[arg-type]
            skill_ref=str(raw["skill_ref"]) if raw.get("skill_ref") else None,
            code_skill_ref=str(raw["code_skill_ref"]) if raw.get("code_skill_ref") else None,
            config=dict(raw.get("config") or {}),
        )


@dataclass(slots=True)
class VibeWorkflowEdge:
    source_node_id: str
    target_node_id: str
    condition: str = ""

    def __post_init__(self) -> None:
        self.source_node_id = str(self.source_node_id).strip()
        self.target_node_id = str(self.target_node_id).strip()
        if not self.source_node_id or not self.target_node_id:
            raise ValueError("edge endpoints are required")
        if self.source_node_id == self.target_node_id:
            raise ValueError(f"self-loop edge on {self.source_node_id!r} is not allowed")
        self.condition = str(self.condition or "")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VibeWorkflowEdge:
        return cls(
            source_node_id=str(raw.get("source_node_id") or ""),
            target_node_id=str(raw.get("target_node_id") or ""),
            condition=str(raw.get("condition") or ""),
        )


@dataclass(slots=True)
class VibeWorkflowGraph:
    workflow_id: str
    name: str = ""
    domain: str = ""
    nodes: list[VibeWorkflowNode] = field(default_factory=list)
    edges: list[VibeWorkflowEdge] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def node(self, node_id: str) -> VibeWorkflowNode:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        raise KeyError(f"node {node_id!r} not in graph")

    def successors(self, node_id: str) -> list[str]:
        return [e.target_node_id for e in self.edges if e.source_node_id == node_id]

    def start_node(self) -> VibeWorkflowNode:
        starts = [n for n in self.nodes if n.node_type == "start"]
        if len(starts) != 1:
            raise ValueError(f"graph must contain exactly one start node, found {len(starts)}")
        return starts[0]

    def end_node(self) -> VibeWorkflowNode:
        ends = [n for n in self.nodes if n.node_type == "end"]
        if len(ends) != 1:
            raise ValueError(f"graph must contain exactly one end node, found {len(ends)}")
        return ends[0]

    def validate(self) -> list[str]:
        """Return a list of human-readable issues; empty list = healthy."""
        issues: list[str] = []
        ids = [n.node_id for n in self.nodes]
        if len(ids) != len(set(ids)):
            issues.append(f"duplicate node ids: {[i for i in ids if ids.count(i) > 1]}")
        starts = [n for n in self.nodes if n.node_type == "start"]
        ends = [n for n in self.nodes if n.node_type == "end"]
        if len(starts) != 1:
            issues.append(f"need exactly one start, got {len(starts)}")
        if len(ends) != 1:
            issues.append(f"need exactly one end, got {len(ends)}")

        node_set = set(ids)
        for e in self.edges:
            if e.source_node_id not in node_set:
                issues.append(f"edge source unknown: {e.source_node_id!r}")
            if e.target_node_id not in node_set:
                issues.append(f"edge target unknown: {e.target_node_id!r}")

        # Reachability from start
        if starts:
            visited: set[str] = set()
            queue: deque[str] = deque([starts[0].node_id])
            while queue:
                cur = queue.popleft()
                if cur in visited:
                    continue
                visited.add(cur)
                for nxt in self.successors(cur):
                    if nxt not in visited:
                        queue.append(nxt)
            unreached = [nid for nid in ids if nid not in visited]
            if unreached:
                issues.append(f"unreachable from start: {unreached}")
            if ends and ends[0].node_id not in visited:
                issues.append(f"end {ends[0].node_id!r} not reachable from start")

        # Cycle detection (DFS three-color)
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in ids}
        adj = {nid: self.successors(nid) for nid in ids}

        def dfs(u: str, path: list[str]) -> None:
            color[u] = GRAY
            path.append(u)
            for v in adj.get(u, []):
                if v not in color:
                    continue
                if color[v] == GRAY:
                    issues.append(f"cycle: {' -> '.join(path[path.index(v):] + [v])}")
                elif color[v] == WHITE:
                    dfs(v, path)
            path.pop()
            color[u] = BLACK

        for nid in ids:
            if color[nid] == WHITE:
                dfs(nid, [])

        return issues

    def topological_order(self) -> list[VibeWorkflowNode]:
        """Return nodes in BFS order from start; raises if not a DAG with start."""
        start = self.start_node()
        order: list[VibeWorkflowNode] = []
        visited: set[str] = set()
        queue: deque[str] = deque([start.node_id])
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            order.append(self.node(cur))
            for nxt in self.successors(cur):
                if nxt not in visited:
                    queue.append(nxt)
        return order

    def code_skill_refs(self) -> Iterable[str]:
        return tuple(n.code_skill_ref for n in self.nodes if n.code_skill_ref)

    def config_skill_refs(self) -> Iterable[str]:
        return tuple(n.skill_ref for n in self.nodes if n.skill_ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "domain": self.domain,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VibeWorkflowGraph:
        return cls(
            workflow_id=str(raw.get("workflow_id") or ""),
            name=str(raw.get("name") or ""),
            domain=str(raw.get("domain") or ""),
            nodes=[VibeWorkflowNode.from_dict(n) for n in raw.get("nodes") or []],
            edges=[VibeWorkflowEdge.from_dict(e) for e in raw.get("edges") or []],
            metadata=dict(raw.get("metadata") or {}),
        )
