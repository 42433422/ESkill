"""Test VibeCoder facade end-to-end."""

from __future__ import annotations

import json

from eskill.vibe_coding import MockLLM, VibeCoder


def _spec(skill_id: str, fn: str = "demo") -> str:
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


def _code(fn: str = "demo") -> str:
    return json.dumps(
        {"source_code": f"def {fn}(x):\n    return {{'y': x}}\n"}
    )


def test_facade_code_generates_skill(tmp_path):
    coder = VibeCoder(
        llm=MockLLM([_spec("s1"), _code()]),
        store_dir=tmp_path,
        llm_for_repair=False,
    )
    skill = coder.code("brief")
    assert skill.skill_id == "s1"
    assert (tmp_path / "code_skill_store.json").exists()


def test_facade_run_executes_skill(tmp_path):
    coder = VibeCoder(
        llm=MockLLM([_spec("s2"), _code()]),
        store_dir=tmp_path,
        llm_for_repair=False,
    )
    skill = coder.code("brief")
    run = coder.run(skill.skill_id, {"x": 42})
    assert run.stage == "static"
    assert run.output_data["y"] == 42


def test_facade_workflow_end_to_end(tmp_path):
    payload = json.dumps(
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
                    "config": {"input_mapping": {"x": "x"}, "output_var": "step1"},
                },
                {
                    "node_id": "n2",
                    "node_type": "eskill",
                    "layer": "code",
                    "code_skill_temp_id": "s2",
                    "config": {"input_mapping": {"x": "step1.y"}, "output_var": "step2"},
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
    coder = VibeCoder(
        llm=MockLLM(
            [payload, _spec("a", "fa"), _code("fa"), _spec("b", "fb"), _code("fb")]
        ),
        store_dir=tmp_path,
        llm_for_repair=False,
    )
    graph = coder.workflow("brief")
    result = coder.execute(graph, {"x": 7})
    assert result.success
    assert result.context["step2"]["y"] == 7


def test_facade_history_and_rollback(tmp_path):
    spec = {
        "skill_id": "extract",
        "name": "extract",
        "domain": "",
        "function_name": "extract",
        "purpose": "demo",
        "signature": {"params": ["user"], "return_type": "dict", "required_params": ["user"]},
        "dependencies": [],
        "test_cases": [
            {
                "case_id": "happy",
                "input_data": {"user": {"name": "ada"}},
                "expected_output": {"name": "ada"},
            }
        ],
        "quality_gate": {"required_keys": ["name"]},
        "domain_keywords": [],
    }
    code = {"source_code": "def extract(user):\n    return {'name': user['name']}\n"}
    coder = VibeCoder(
        llm=MockLLM([json.dumps(spec), json.dumps(code)]),
        store_dir=tmp_path,
        llm_for_repair=False,
    )
    skill = coder.code("brief")
    coder.run(skill.skill_id, {"user": {}})  # triggers heal

    chain = coder.evolution_chain(skill.skill_id)
    assert len(chain) == 2

    coder.rollback(skill.skill_id, 1)
    rolled = coder.list_code_skills()[0]
    assert rolled.active_version == 1


def test_facade_report_contains_totals(tmp_path):
    coder = VibeCoder(
        llm=MockLLM([_spec("s3"), _code()]),
        store_dir=tmp_path,
        llm_for_repair=False,
    )
    coder.code("brief")
    report = coder.report()
    assert report["totals"]["skills"] >= 1


def test_facade_lists_skills(tmp_path):
    coder = VibeCoder(
        llm=MockLLM([_spec("s4"), _code()]),
        store_dir=tmp_path,
        llm_for_repair=False,
    )
    coder.code("brief")
    assert any(s.skill_id == "s4" for s in coder.list_code_skills())
