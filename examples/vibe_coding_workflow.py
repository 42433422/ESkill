"""End-to-end workflow demo.

A single brief becomes a multi-node graph in which every code-layer node is
generated and verified before the graph is returned. The engine then runs
the graph; failures inside any node trigger the underlying CodeSkillRuntime
to patch and solidify on the fly.

Mock LLM canned responses simulate:

1. Workflow plan (one round)
2. Each code skill: spec then code (two rounds per skill)
"""

from __future__ import annotations

import json
import tempfile

from eskill.vibe_coding import MockLLM, VibeCoder

WORKFLOW_BRIEF = "做一个员工：先把传入字符串转大写，再加上感叹号"

WORKFLOW_PAYLOAD = json.dumps(
    {
        "workflow_id": "shouty",
        "name": "Shouty pipeline",
        "domain": "字符串处理",
        "code_skills": [
            {"temp_id": "step1", "skill_brief": "把 input.text 转大写"},
            {"temp_id": "step2", "skill_brief": "在末尾加感叹号"},
        ],
        "nodes": [
            {"node_id": "start", "node_type": "start"},
            {
                "node_id": "n1",
                "node_type": "eskill",
                "layer": "code",
                "code_skill_temp_id": "step1",
                "name": "uppercase",
                "config": {"input_mapping": {"text": "text"}, "output_var": "uppercased"},
            },
            {
                "node_id": "n2",
                "node_type": "eskill",
                "layer": "code",
                "code_skill_temp_id": "step2",
                "name": "shout",
                "config": {"input_mapping": {"text": "uppercased.text"}, "output_var": "shouted"},
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


def _spec(skill_id: str, fn: str) -> str:
    return json.dumps(
        {
            "skill_id": skill_id,
            "name": skill_id,
            "domain": "",
            "function_name": fn,
            "purpose": skill_id,
            "signature": {"params": ["text"], "return_type": "dict", "required_params": ["text"]},
            "dependencies": [],
            "test_cases": [
                {
                    "case_id": "happy",
                    "input_data": {"text": "hi"},
                    "expected_output": {"text": ("HI" if fn == "to_upper" else "hi!")},
                }
            ],
            "quality_gate": {"required_keys": ["text"]},
            "domain_keywords": [],
        }
    )


def _code(fn: str) -> str:
    if fn == "to_upper":
        body = "    if not isinstance(text, str):\n        return {'text': '', 'error': 'text_not_str'}\n    return {'text': text.upper()}\n"
    else:
        body = "    if not isinstance(text, str):\n        return {'text': '', 'error': 'text_not_str'}\n    return {'text': text + '!'}\n"
    return json.dumps({"source_code": f"def {fn}(text):\n{body}"})


def build_mock_llm() -> MockLLM:
    return MockLLM(
        [
            WORKFLOW_PAYLOAD,
            _spec("uppercase", "to_upper"),
            _code("to_upper"),
            _spec("shout", "exclaim"),
            _code("exclaim"),
        ]
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        coder = VibeCoder(llm=build_mock_llm(), store_dir=tmp, llm_for_repair=False)
        report = coder.workflow_with_report(WORKFLOW_BRIEF)
        graph = report.graph
        print("Workflow:", graph.workflow_id)
        for n in graph.nodes:
            print(
                f"  {n.node_id:8s} {n.node_type:8s} layer={n.layer or '-':6s} "
                f"code_skill_ref={n.code_skill_ref or '-'}"
            )
        print("Code skills created:", report.code_skills_created)

        result = coder.execute(graph, {"text": "hello"})
        print("\nWorkflow run success:", result.success, "duration_ms:", result.duration_ms)
        for outcome in result.outcomes:
            print(
                f"  {outcome.node_id} -> stage={outcome.stage} output={outcome.output}"
            )
        print("Final context:", json.dumps(result.context, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
