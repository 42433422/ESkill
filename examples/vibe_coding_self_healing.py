"""Self-healing demo: feed bad input, watch the runtime patch + solidify v2.

The first generation produces working code, but it explodes on a key it
didn't anticipate. The runtime's rule-based patch generator wraps the unsafe
subscript access into a ``.get`` call, the patched code passes all test cases
in the sandbox, and the result is solidified as version 2.
"""

from __future__ import annotations

import json
import tempfile

from eskill.vibe_coding import MockLLM, VibeCoder


def build_mock_llm() -> MockLLM:
    spec = {
        "skill_id": "extract-name",
        "name": "Extract Name",
        "domain": "",
        "function_name": "extract_name",
        "purpose": "把 user dict 里的 name 字段读出来",
        "signature": {
            "params": ["user"],
            "return_type": "dict",
            "required_params": ["user"],
        },
        "dependencies": [],
        "test_cases": [
            {
                "case_id": "happy",
                "input_data": {"user": {"name": "ada"}},
                "expected_output": {"name": "ada"},
            },
        ],
        "quality_gate": {"required_keys": ["name"]},
        "domain_keywords": [],
    }
    naive_code = {
        "source_code": (
            "def extract_name(user):\n"
            "    return {'name': user['name']}\n"
        )
    }
    return MockLLM([json.dumps(spec), json.dumps(naive_code)])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        coder = VibeCoder(llm=build_mock_llm(), store_dir=tmp, llm_for_repair=False)
        skill = coder.code("把 user 里的 name 转成大写", mode="brief_first")
        print("v1 source:")
        print(skill.get_active_version().source_code)

        # Feed an input that breaks the naive subscript code
        bad_input = {"user": {}}
        run = coder.run(skill.skill_id, bad_input)
        print("\nRun stage:", run.stage)
        print("Run output:", run.output_data)
        if run.error:
            print("Run error:", run.error)
        if run.patch:
            print("Patch reason:", run.patch.reason)
            print("Patched code:\n", run.patch.patched_code)

        latest = coder.list_code_skills()[0]
        print("\nactive_version after run:", latest.active_version)
        for v in latest.versions:
            print(f"  - v{v.version} ({v.source_run_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
