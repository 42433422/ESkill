"""Test NLWorkflowFactory: NL → multi-skill workflow graph."""

from __future__ import annotations

import json

import pytest

from eskill import JsonCodeSkillStore
from eskill.vibe_coding import MockLLM
from eskill.vibe_coding.code_factory import NLCodeSkillFactory
from eskill.vibe_coding.workflow_factory import NLWorkflowFactory, VibeCodingError


def _spec(skill_id: str, fn: str) -> str:
    return json.dumps(
        {
            "skill_id": skill_id,
            "name": skill_id,
            "domain": "",
            "function_name": fn,
            "purpose": "demo",
            "signature": {"params": ["x"], "return_type": "dict", "required_params": ["x"]},
            "dependencies": [],
            "test_cases": [
                {"case_id": "happy", "input_data": {"x": 1}, "expected_output": {"y": 1}}
            ],
            "quality_gate": {"required_keys": ["y"]},
            "domain_keywords": [],
        }
    )


def _code(fn: str) -> str:
    return json.dumps(
        {"source_code": f"def {fn}(x):\n    return {{'y': x}}\n"}
    )


def _workflow_payload() -> str:
    return json.dumps(
        {
            "workflow_id": "wf",
            "name": "wf",
            "domain": "demo",
            "code_skills": [
                {"temp_id": "s1", "skill_brief": "step one"},
                {"temp_id": "s2", "skill_brief": "step two"},
            ],
            "nodes": [
                {"node_id": "start", "node_type": "start"},
                {"node_id": "n1", "node_type": "eskill", "layer": "code", "code_skill_temp_id": "s1"},
                {"node_id": "n2", "node_type": "eskill", "layer": "code", "code_skill_temp_id": "s2"},
                {"node_id": "end", "node_type": "end"},
            ],
            "edges": [
                {"source_node_id": "start", "target_node_id": "n1"},
                {"source_node_id": "n1", "target_node_id": "n2"},
                {"source_node_id": "n2", "target_node_id": "end"},
            ],
        }
    )


def _build_factory(tmp_path, llm: MockLLM) -> NLWorkflowFactory:
    store = JsonCodeSkillStore(tmp_path / "code.json")
    code_factory = NLCodeSkillFactory(llm, store)
    return NLWorkflowFactory(llm, code_factory)


def test_workflow_generates_graph_and_skills(tmp_path):
    llm = MockLLM(
        [
            _workflow_payload(),
            _spec("wf-s1", "to_y_a"),
            _code("to_y_a"),
            _spec("wf-s2", "to_y_b"),
            _code("to_y_b"),
        ]
    )
    factory = _build_factory(tmp_path, llm)
    report = factory.generate_with_report("any brief")
    assert report.workflow_id == "wf"
    assert len(report.code_skills_created) == 2
    assert len(report.graph.nodes) == 4
    # Each eskill node has a real skill ref mapped from its temp id
    assert {n.code_skill_ref for n in report.graph.nodes if n.code_skill_ref} == set(
        report.code_skills_created
    )


def test_workflow_validates_graph_structure(tmp_path):
    """Missing end node must fail with an error."""
    bad_payload = json.dumps(
        {
            "workflow_id": "bad",
            "code_skills": [{"temp_id": "s1", "skill_brief": "x"}],
            "nodes": [
                {"node_id": "start", "node_type": "start"},
                {"node_id": "n1", "node_type": "eskill", "layer": "code", "code_skill_temp_id": "s1"},
            ],
            "edges": [
                {"source_node_id": "start", "target_node_id": "n1"},
            ],
        }
    )
    llm = MockLLM([bad_payload, _spec("bad-s1", "fn"), _code("fn")])
    factory = _build_factory(tmp_path, llm)
    with pytest.raises(VibeCodingError):
        factory.generate("brief")


def test_workflow_unknown_temp_id_raises(tmp_path):
    bad = json.dumps(
        {
            "workflow_id": "bad",
            "code_skills": [{"temp_id": "s1", "skill_brief": "x"}],
            "nodes": [
                {"node_id": "start", "node_type": "start"},
                {"node_id": "n1", "node_type": "eskill", "layer": "code", "code_skill_temp_id": "unknown"},
                {"node_id": "end", "node_type": "end"},
            ],
            "edges": [
                {"source_node_id": "start", "target_node_id": "n1"},
                {"source_node_id": "n1", "target_node_id": "end"},
            ],
        }
    )
    llm = MockLLM([bad, _spec("bad-s1", "fn"), _code("fn")])
    factory = _build_factory(tmp_path, llm)
    # Will raise because the unknown temp_id can't be resolved
    with pytest.raises(VibeCodingError):
        factory.generate("brief")
