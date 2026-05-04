"""Minimal vibe-coding demo.

A natural-language brief becomes a sandbox-validated CodeSkill in three lines.
Uses a deterministic MockLLM so the example runs offline; in production swap
in OpenAILLM and remove the mock setup.
"""

from __future__ import annotations

import json
import tempfile

from eskill.vibe_coding import MockLLM, VibeCoder


def build_mock_llm() -> MockLLM:
    spec = {
        "skill_id": "string-reverse",
        "name": "字符串反转",
        "domain": "字符串处理",
        "function_name": "reverse_string",
        "purpose": "把任意字符串原地反转",
        "signature": {
            "params": ["text"],
            "return_type": "dict",
            "required_params": ["text"],
        },
        "dependencies": [],
        "test_cases": [
            {"case_id": "happy", "input_data": {"text": "hello"}, "expected_output": {"reversed": "olleh"}},
            {"case_id": "empty", "input_data": {"text": ""}, "expected_output": {"reversed": ""}},
            {"case_id": "unicode", "input_data": {"text": "你好"}, "expected_output": {"reversed": "好你"}},
        ],
        "quality_gate": {"required_keys": ["reversed"]},
        "domain_keywords": ["反转", "reverse"],
    }
    code = {
        "source_code": (
            "def reverse_string(text):\n"
            "    if not isinstance(text, str):\n"
            "        return {'reversed': '', 'error': 'text_not_str'}\n"
            "    return {'reversed': text[::-1]}\n"
        )
    }
    return MockLLM([json.dumps(spec), json.dumps(code)])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        coder = VibeCoder(llm=build_mock_llm(), store_dir=tmp, llm_for_repair=False)
        skill = coder.code("写一个把字符串反转的函数")
        print("Generated skill:", skill.skill_id, "v", skill.active_version)
        print(skill.get_active_version().source_code)

        run = coder.run(skill.skill_id, {"text": "vibe coding"})
        print("\nRuntime stage:", run.stage)
        print("Output:", run.output_data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
