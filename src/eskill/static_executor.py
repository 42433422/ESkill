"""Shared static logic execution and quality gates (no store / no I/O).

Used by runtime, async runtime, and isolated sandbox workers to avoid drift.
"""

from __future__ import annotations

import json
import re
from string import Template
from typing import Any


def render_template(template: str, values: dict[str, Any]) -> str:
    flat = {key: "" if value is None else str(value) for key, value in values.items()}
    rendered = Template(template).safe_substitute(flat)
    return re.sub(r"\s+", " ", rendered).strip()


def execute_tool_call(step: dict[str, Any], context: dict[str, Any]) -> Any:
    tool = str(step.get("tool") or "")
    if tool == "echo":
        return step.get("args", context)
    if tool == "extract_keys":
        return sorted(context.keys())
    raise ValueError(f"Tool is not in ESkill allowlist: {tool}")


def execute_pipeline(logic: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
    context = {**input_data}
    output: dict[str, Any] = {"logic_type": "pipeline"}
    steps = logic.get("steps") or []
    if not isinstance(steps, list):
        raise ValueError("pipeline steps must be a list")
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"pipeline step #{idx} must be an object")
        step_type = str(step.get("type") or "template_transform")
        output_var = str(step.get("output_var") or step.get("id") or f"step_{idx}")
        if step_type == "template_transform":
            value = render_template(str(step.get("template") or ""), context)
        elif step_type == "set_value":
            value = step.get("value")
        elif step_type == "tool_call":
            value = execute_tool_call(step, context)
        else:
            raise ValueError(f"Unsupported pipeline step type: {step_type}")
        output[output_var] = value
        context[output_var] = value
    return output


def execute_static_logic(logic: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
    logic_type = logic.get("type") or "template_transform"
    required = [str(x) for x in logic.get("required_fields") or []]
    missing = [field for field in required if input_data.get(field) in (None, "")]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    if logic_type == "template_transform":
        rendered = render_template(str(logic.get("template") or ""), input_data)
        output_var = str(logic.get("output_var") or "result")
        return {output_var: rendered, "logic_type": logic_type}

    if logic_type == "employee_task":
        template = str(logic.get("task_template") or logic.get("task") or "")
        output_var = str(logic.get("output_var") or "employee_result")
        return {
            output_var: {
                "task": render_template(template, input_data),
                "simulated": True,
            },
            "logic_type": logic_type,
        }

    if logic_type == "pipeline":
        return execute_pipeline(logic, input_data)

    raise ValueError(f"Unsupported static logic type: {logic_type}")


def quality_report(output: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    score = 1.0
    min_length = int(gate.get("min_length") or 0)
    text = " ".join(
        str(v)
        for k, v in output.items()
        if k not in {"logic_type", "eskill_logic_type", "solidified_version"}
    )
    if min_length > 0 and len(text) < min_length:
        issues.append(f"min_length:{len(text)}<{min_length}")
        score = min(score, len(text) / max(min_length, 1))
    for key in [str(x) for x in gate.get("required_keys") or []]:
        if key not in output:
            issues.append(f"missing_key:{key}")
            score = min(score, 0.6)
    for token in [str(x) for x in gate.get("contains_all") or []]:
        if token and token not in text:
            issues.append(f"missing_text:{token}")
            score = min(score, 0.6)
    any_tokens = [str(x) for x in gate.get("contains_any") or []]
    if any_tokens and not any(token in text for token in any_tokens):
        issues.append("missing_any_text")
        score = min(score, 0.7)
    min_score = float(gate.get("min_score") or 0.0)
    return {"passed": not issues and score >= min_score, "score": round(score, 4), "issues": issues}


def logic_summary_for_analysis(logic: dict[str, Any]) -> str:
    """Compact JSON for embedding in analysis / LLM prompts."""
    try:
        return json.dumps(logic, ensure_ascii=False, sort_keys=True)[:8000]
    except (TypeError, ValueError):
        return str(logic)[:8000]
