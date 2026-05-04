"""Test VibeWorkflowEngine end-to-end execution."""

from __future__ import annotations

import json

from eskill import CodeSkillRuntime, JsonCodeSkillStore
from eskill.vibe_coding import MockLLM
from eskill.vibe_coding.code_factory import NLCodeSkillFactory
from eskill.vibe_coding.workflow_engine import VibeWorkflowEngine
from eskill.vibe_coding.workflow_factory import NLWorkflowFactory


def _spec(skill_id: str, fn: str) -> str:
    return json.dumps(
        {
            "skill_id": skill_id,
            "name": skill_id,
            "domain": "",
            "function_name": fn,
            "purpose": "demo",
            "signature": {"params": ["text"], "return_type": "dict", "required_params": ["text"]},
            "dependencies": [],
            "test_cases": [
                {"case_id": "happy", "input_data": {"text": "hi"}, "expected_output": {"text": "hi"}}
            ],
            "quality_gate": {"required_keys": ["text"]},
            "domain_keywords": [],
        }
    )


def _identity_code(fn: str) -> str:
    return json.dumps(
        {"source_code": f"def {fn}(text):\n    return {{'text': str(text)}}\n"}
    )


def _wf_payload() -> str:
    return json.dumps(
        {
            "workflow_id": "wf",
            "code_skills": [
                {"temp_id": "s1", "skill_brief": "x"},
                {"temp_id": "s2", "skill_brief": "y"},
            ],
            "nodes": [
                {"node_id": "start", "node_type": "start"},
                {
                    "node_id": "n1",
                    "node_type": "eskill",
                    "layer": "code",
                    "code_skill_temp_id": "s1",
                    "config": {"input_mapping": {"text": "text"}, "output_var": "step1"},
                },
                {
                    "node_id": "n2",
                    "node_type": "eskill",
                    "layer": "code",
                    "code_skill_temp_id": "s2",
                    "config": {"input_mapping": {"text": "step1.text"}, "output_var": "step2"},
                },
                {"node_id": "end", "node_type": "end"},
            ],
            "edges": [
                {"source_node_id": "start", "target_node_id": "n1"},
                {"source_node_id": "n1", "target_node_id": "n2"},
                {"source_node_id": "n2", "target_node_id": "end"},
            ],
        }
    )


def test_engine_runs_workflow_to_completion(tmp_path):
    store = JsonCodeSkillStore(tmp_path / "code.json")
    llm = MockLLM(
        [
            _wf_payload(),
            _spec("wf-s1", "fn1"),
            _identity_code("fn1"),
            _spec("wf-s2", "fn2"),
            _identity_code("fn2"),
        ]
    )
    factory = NLWorkflowFactory(llm, NLCodeSkillFactory(llm, store))
    graph = factory.generate("brief")

    runtime = CodeSkillRuntime(store)
    engine = VibeWorkflowEngine(code_runtime=runtime)
    result = engine.run(graph, {"text": "hello"})
    assert result.success
    assert result.context["step1"] == {"text": "hello"}
    assert result.context["step2"] == {"text": "hello"}
    assert len(result.outcomes) == 2
    assert all(o.stage == "static" for o in result.outcomes)


def test_engine_dotted_input_mapping(tmp_path):
    store = JsonCodeSkillStore(tmp_path / "code.json")
    llm = MockLLM(
        [
            _wf_payload(),
            _spec("wf-s1", "fn1"),
            _identity_code("fn1"),
            _spec("wf-s2", "fn2"),
            _identity_code("fn2"),
        ]
    )
    factory = NLWorkflowFactory(llm, NLCodeSkillFactory(llm, store))
    graph = factory.generate("brief")
    runtime = CodeSkillRuntime(store)
    engine = VibeWorkflowEngine(code_runtime=runtime)
    result = engine.run(graph, {"text": "abc"})
    # n2 input is "step1.text" → "abc"; engine resolves correctly
    assert result.success
    assert result.context["step2"] == {"text": "abc"}


def test_engine_reports_validation_failure(tmp_path):
    """Engine refuses to run an invalid graph."""
    from eskill.vibe_coding.workflow_models import (
        VibeWorkflowEdge,
        VibeWorkflowGraph,
        VibeWorkflowNode,
    )

    graph = VibeWorkflowGraph(
        workflow_id="bad",
        nodes=[
            VibeWorkflowNode(node_id="start", node_type="start"),
            VibeWorkflowNode(
                node_id="n1",
                node_type="eskill",
                layer="code",
                code_skill_ref="missing",
            ),
            # No end node — should fail validation
        ],
        edges=[VibeWorkflowEdge(source_node_id="start", target_node_id="n1")],
    )
    store = JsonCodeSkillStore(tmp_path / "code.json")
    runtime = CodeSkillRuntime(store)
    engine = VibeWorkflowEngine(code_runtime=runtime)
    result = engine.run(graph, {})
    assert not result.success
    assert "validation" in result.error.lower() or "end" in result.error.lower()


def test_engine_requires_at_least_one_runtime():
    import pytest

    with pytest.raises(ValueError):
        VibeWorkflowEngine()
