"""Lightweight program analysis (stdlib only) for static_logic and optional snippets."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from typing import Any

from .static_executor import logic_summary_for_analysis


@dataclass(slots=True)
class AnalysisReport:
    logic_type: str
    summary: str
    pipeline_step_count: int = 0
    required_fields: list[str] = field(default_factory=list)
    ast_summary: dict[str, Any] | None = None
    risk_flags: list[str] = field(default_factory=list)
    invariants_suggested: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AnalysisReport:
        return cls(
            logic_type=str(raw.get("logic_type") or ""),
            summary=str(raw.get("summary") or ""),
            pipeline_step_count=int(raw.get("pipeline_step_count") or 0),
            required_fields=[str(x) for x in raw.get("required_fields") or []],
            ast_summary=dict(raw["ast_summary"]) if isinstance(raw.get("ast_summary"), dict) else None,
            risk_flags=[str(x) for x in raw.get("risk_flags") or []],
            invariants_suggested=[str(x) for x in raw.get("invariants_suggested") or []],
        )


def analyze_static_logic(static_logic: dict[str, Any]) -> dict[str, Any]:
    logic_type = str(static_logic.get("type") or "template_transform")
    required = [str(x) for x in static_logic.get("required_fields") or []]
    steps = static_logic.get("steps") or []
    step_count = len(steps) if isinstance(steps, list) else 0

    risk_flags: list[str] = []
    if step_count > 12:
        risk_flags.append("large_pipeline")
    if static_logic.get("allow_steps"):
        risk_flags.append("dynamic_pipeline_allowed")

    snippet = ""
    meta = static_logic.get("metadata")
    if isinstance(meta, dict):
        snippet = str(meta.get("python_snippet") or meta.get("python_logic") or "")

    ast_summary = _analyze_python_snippet(snippet) if snippet.strip() else None
    if ast_summary and ast_summary.get("has_eval_or_exec"):
        risk_flags.append("dangerous_ast")

    invariants: list[str] = []
    if logic_type == "pipeline" and step_count:
        invariants.append("Each pipeline step should preserve required context keys.")
    if required:
        invariants.append(f"Inputs must supply: {', '.join(required)}")

    report = AnalysisReport(
        logic_type=logic_type,
        summary=logic_summary_for_analysis(static_logic),
        pipeline_step_count=step_count,
        required_fields=list(required),
        ast_summary=ast_summary,
        risk_flags=risk_flags,
        invariants_suggested=invariants,
    )
    return report.to_dict()


def _analyze_python_snippet(source: str) -> dict[str, Any] | None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"parse_error": str(exc), "has_eval_or_exec": False}

    funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    calls: list[str] = []
    bad = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
                if node.func.id in ("eval", "exec", "__import__"):
                    bad = True
            elif isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
    return {
        "functions": funcs[:32],
        "call_names_sample": sorted(set(calls))[:48],
        "has_eval_or_exec": bad,
    }
